#!/usr/bin/env python3
"""
sshauto — SSH-over-websocket relay autoscript.

Usage:
    python3 main.py install               full automated first-time setup
    python3 main.py install --only nginx_relay,firewall
    python3 main.py update                git pull + re-apply (manual trigger;
                                           normally the 30s timer does this)
    python3 main.py status                show each feature's install state
    python3 main.py cert                  (re)run the certificate wizard
    python3 main.py uninstall             best-effort teardown
    python3 main.py dashboard             same as typing `kk`
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import state  # noqa: E402
from core.exceptions import SSHAutoError  # noqa: E402
from core.logger import log  # noqa: E402
from core.plugin_manager import PluginManager  # noqa: E402
from core.shell import Shell  # noqa: E402

BANNER = r"""
   _____ _____ _   _   ___         _
  / ____/ ____| | | | / _ \       | |
 | (___| (___ | |_| |/ /_\ \_   _ | |_ ___
  \___ \\___ \|  _  ||  _  | | | || __/ _ \
  ____) |___) | | | || | | | |_| || || (_) |
 |_____/_____/|_| |_|\_| |_/\__,_| \__\___/

   websocket relay autoscript — type 'kk' any time for the dashboard
"""

# Certificates are deliberately excluded from a blanket "install everything"
# so re-issuing a cert is always an explicit operator action.
NON_IDEMPOTENT = {"certificates"}


def require_root():
    if os.geteuid() != 0:
        log.critical("this program must run as root (it edits /etc, systemd units, iptables)")
        sys.exit(1)


def cmd_install(args):
    require_root()
    if not args.quiet:
        print(BANNER)

    manager = PluginManager()
    only = args.only.split(",") if args.only else None

    if args.skip_non_idempotent:
        only = [n for n in (only or manager.names()) if n not in NON_IDEMPOTENT]

    data = state.ensure_defaults()
    if not data.get("created_at"):
        data["created_at"] = datetime.datetime.utcnow().isoformat()
        state.save(data)

    results = manager.install_all(only=only)

    if not args.quiet and not args.skip_non_idempotent:
        _install_kk_command()
        log.rule("done")
        log.important("Setup complete. Type 'kk' any time to open the dashboard.")
        failures = [n for n, ok in results.items() if not ok]
        if failures:
            log.warning(f"Some features failed: {', '.join(failures)}. "
                        f"Run 'python3 main.py status' for details.")


def cmd_update(args):
    require_root()
    log.info("manually triggering the same check the 30s timer runs")
    Shell.run(f"{sys.executable} {os.path.dirname(__file__)}/scripts/autoupdate_check.py")


def cmd_status(args):
    PluginManager().status_all()


def cmd_cert(args):
    require_root()
    from features.certificates import CertificatesFeature
    CertificatesFeature().install()


def cmd_uninstall(args):
    require_root()
    manager = PluginManager()
    for name in reversed(manager.names()):
        try:
            manager.get(name).remove()
            log.success(f"{name} removed")
        except Exception as exc:  # noqa: BLE001
            log.error(f"{name} teardown failed: {exc}")


def cmd_dashboard(args):
    require_root()
    from dashboard.app import Dashboard
    Dashboard().run()


def _install_kk_command():
    """Symlinks the `kk` word to launch the dashboard from any shell."""
    from core.config import APP_ROOT
    wrapper = "/usr/local/bin/kk"
    try:
        with open(wrapper, "w") as f:
            f.write(f"#!/usr/bin/env bash\nexec python3 {APP_ROOT}/main.py dashboard \"$@\"\n")
        os.chmod(wrapper, 0o755)
        log.debug(f"installed launcher at {wrapper}")
    except OSError as exc:
        log.warning(f"could not install the 'kk' shortcut: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sshauto")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="run full automated setup")
    p_install.add_argument("--only", help="comma-separated feature names")
    p_install.add_argument("--skip-non-idempotent", action="store_true",
                            help="used by the auto-updater; skips certificates")
    p_install.add_argument("--quiet", action="store_true")
    p_install.set_defaults(func=cmd_install)

    p_update = sub.add_parser("update", help="manually trigger a git-update check")
    p_update.set_defaults(func=cmd_update)

    p_status = sub.add_parser("status", help="show install status of every feature")
    p_status.set_defaults(func=cmd_status)

    p_cert = sub.add_parser("cert", help="(re)run the certificate wizard")
    p_cert.set_defaults(func=cmd_cert)

    p_uninstall = sub.add_parser("uninstall", help="best-effort teardown")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_dash = sub.add_parser("dashboard", help="open the kk dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except SSHAutoError as exc:
        log.critical(exc.message)
        if exc.hint:
            log.warning(exc.hint)
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        log.warning("interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
