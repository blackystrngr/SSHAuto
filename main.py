#!/usr/bin/env python3
"""
sshauto — SSH-over-websocket relay autoscript.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import __version__, doctor
from core.config import state
from core.exceptions import SSHAutoError
from core.logger import log
from core.plugin_manager import PluginManager
from core.services import restart_all_services
from core.shell import Shell

BANNER = r"""
   _____ _____ _   _   ___         _
  / ____/ ____| | | | / _ \       | |
 | (___| (___ | |_| |/ /_\ \_   _ | |_ ___
  \___ \\___ \|  _  ||  _  | | | || __/ _ \
  ____) |___) | | | || | | | |_| || || (_) |
 |_____/_____/|_| |_|\_| |_/\__,_| \__\___/

   websocket relay autoscript — type 'kk' any time for the dashboard
"""

NON_IDEMPOTENT = {"certificates"}

def require_root():
    if os.geteuid() != 0:
        log.critical("this program must run as root")
        sys.exit(1)

def _clean_old_proxy():
    log.info("Cleaning up old proxy files...")
    for svc in ["ws-ssh-proxy", "sshauto-proxy"]:
        Shell.run(f"systemctl stop {svc}", check=False, timeout=5)
        Shell.run(f"systemctl disable {svc}", check=False, timeout=5)
    for path in ["/usr/local/bin/ws_ssh_proxy.py", "/usr/local/bin/ws_proxy.py"]:
        p = Path(path)
        if p.exists():
            p.unlink()
            log.debug(f"removed {path}")
    for path in ["/etc/systemd/system/ws-ssh-proxy.service",
                 "/etc/systemd/system/sshauto-proxy.service"]:
        p = Path(path)
        if p.exists():
            p.unlink()
            log.debug(f"removed {path}")
    Shell.run("systemctl daemon-reload", check=False, timeout=5)
    log.success("Old proxy files cleaned.")

def cmd_install(args):
    require_root()
    if not args.quiet:
        print(BANNER)
    _clean_old_proxy()
    manager = PluginManager()
    only = args.only.split(",") if args.only else None
    if args.skip_non_idempotent:
        only = [n for n in (only or manager.default_names()) if n not in NON_IDEMPOTENT]
    data = state.ensure_defaults()
    include = []
    if args.panel:
        _apply_panel_args(args)
        include.append("web_panel")
    if args.filter:
        _apply_filter_args(args)
        include.append("content_filter")
    if args.squid:
        _apply_squid_args(args)
        include.append("squid_proxy")
    if not data.get("created_at"):
        data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state.save(data)
    results = manager.install_all(only=only, force=args.force, include=include)
    if not args.quiet and not args.skip_non_idempotent:
        _install_kk_command()
        log.rule("done")
        log.important("Setup complete. Type 'kk' any time to open the dashboard.")
        failures = [n for n, ok in results.items() if not ok]
        if failures:
            log.warning(f"Some features failed: {', '.join(failures)}.")
    log.info("Reloading systemd and restarting services...")
    Shell.run("systemctl daemon-reload", check=False, timeout=10)
    restart_all_services()
    if not args.only and not doctor.run(heal=True):
        sys.exit(1)

def _apply_squid_args(args):
    data = state.ensure_defaults()
    if args.squid_user:
        data["squid_user"] = args.squid_user
    if args.squid_pass:
        data["squid_pass"] = args.squid_pass
    if args.squid_port:
        data["squid_port"] = args.squid_port
    state.save(data)

def cmd_squid(args):
    require_root()
    manager = PluginManager()
    feature = manager.get("squid_proxy")
    if args.remove:
        feature.remove()
        return
    _apply_squid_args(args)
    if not feature.safe_install():
        sys.exit(1)

def cmd_doctor(args):
    require_root()
    if not doctor.run(heal=not args.no_heal):
        sys.exit(1)

def _apply_filter_args(args):
    from core.blocklists import CATEGORIES
    from core.exceptions import ValidationError
    data = state.ensure_defaults()
    if args.categories is not None:
        raw = args.categories.strip().lower()
        wanted = list(CATEGORIES) if raw == "all" else [] if raw in ("", "none") else \
            [c.strip() for c in raw.split(",") if c.strip()]
        bad = [c for c in wanted if c not in CATEGORIES]
        if bad:
            raise ValidationError(f"unknown categories: {', '.join(bad)}",
                                  hint=f"choose from: {', '.join(CATEGORIES)}, all, none")
        data["filter_categories"] = wanted
    if args.allow is not None:
        data["filter_allow"] = [d.strip().lower() for d in args.allow.split(",") if d.strip()]
    if args.block_private is not None:
        data["filter_private"] = args.block_private
    state.save(data)

def cmd_filter(args):
    require_root()
    feature = PluginManager().get("content_filter")
    if args.remove:
        feature.remove()
    elif args.status:
        print("\n".join(feature.summary()))
    elif args.update:
        feature.update()
    else:
        _apply_filter_args(args)
        if not feature.safe_install():
            sys.exit(1)

def _apply_panel_args(args):
    from core.exceptions import ValidationError
    from panel.auth import Auth, generate_password
    data = state.ensure_defaults()
    auth = Auth()
    user = args.panel_user or data.get("panel_user") or "admin"
    password = args.panel_pass or (generate_password() if args.reset_password else None)
    if password:
        if len(password) < 10:
            raise ValidationError("panel password needs at least 10 characters")
        auth.set_credentials(user, password)
        data = state.load()
        data["panel_user"] = user
        state.save(data)
        log.important(f"Panel login: {user} / {password}")
    elif args.panel_user and auth.configured():
        data["panel_user"] = user
        state.save(data)

def cmd_panel(args):
    require_root()
    feature = PluginManager().get("web_panel")
    if args.remove:
        feature.remove()
    elif args.status:
        from features.web_panel import panel_url
        data = state.load()
        print(f"service: {'active' if feature.is_installed() else 'not running'}")
        print(f"url:     {panel_url(data) or 'not configured'}")
        print(f"user:    {data.get('panel_user') or '-'}")
    else:
        _apply_panel_args(args)
        if not feature.safe_install():
            sys.exit(1)

def cmd_serve_panel(args):
    from panel.server import serve
    serve()

def cmd_status(args):
    PluginManager().status_all()

def cmd_cert(args):
    require_root()
    from features.certificates import CertificatesFeature
    CertificatesFeature().interactive()

def cmd_uninstall(args):
    require_root()
    from core import uninstall
    uninstall.run(yes=args.yes, purge_packages=args.purge_packages, keep_users=args.keep_users)

def cmd_dashboard(args):
    require_root()
    from dashboard.app import Dashboard
    Dashboard().run()

def _install_kk_command():
    from core.config import APP_ROOT
    wrapper = "/usr/local/bin/kk"
    try:
        with open(wrapper, "w") as f:
            f.write(f"#!/usr/bin/env bash\nexec python3 {APP_ROOT}/main.py dashboard \"$@\"\n")
        os.chmod(wrapper, 0o755)
        log.debug(f"installed launcher at {wrapper}")
    except OSError as exc:
        log.warning(f"could not install 'kk': {exc}")

def _add_squid_opts(p):
    p.add_argument("--squid-user", help="squid username (default: didntgo)")
    p.add_argument("--squid-pass", help="squid password (default: didntgo1#)")
    p.add_argument("--squid-port", type=int, help="squid port (default: 3128)")

def _add_filter_opts(p):
    p.add_argument("--categories",
                   help="comma list of ads,malware,phishing,cryptominers | all | none (default: all)")
    p.add_argument("--allow", help="comma list of domains to never block (replaces the list)")
    p.add_argument("--block-private", action=argparse.BooleanOptionalAction, default=None,
                   help="block tunnel access to private/link-local networks (incl. cloud metadata)")

def _add_panel_opts(p):
    p.add_argument("--panel-user", help="panel username (default: admin)")
    p.add_argument("--panel-pass", help="panel password, at least 10 characters (default: random, shown once)")
    p.add_argument("--reset-password", action="store_true", help="set a new random panel password")

def build_parser():
    parser = argparse.ArgumentParser(prog="sshauto")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"sshauto {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="full automated setup")
    p.add_argument("--only", help="comma-separated features")
    p.add_argument("--skip-non-idempotent", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--force", action="store_true", help="overwrite all configs")
    p.add_argument("--squid", action="store_true", help="also install the optional Squid proxy")
    _add_squid_opts(p)
    p.add_argument("--filter", action="store_true", help="also install the optional DNS content filter")
    _add_filter_opts(p)
    p.add_argument("--panel", action="store_true", help="also install the optional web panel")
    _add_panel_opts(p)
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("squid", help="install/reconfigure the optional Squid proxy")
    p.add_argument("--remove", action="store_true", help="disable Squid")
    _add_squid_opts(p)
    p.set_defaults(func=cmd_squid)
    p = sub.add_parser("panel", help="optional web panel (monitor and manage in a browser)")
    _add_panel_opts(p)
    p.add_argument("--status", action="store_true", help="show the panel URL and user")
    p.add_argument("--remove", action="store_true", help="remove the panel")
    p.set_defaults(func=cmd_panel)
    sub.add_parser("serve-panel", help=argparse.SUPPRESS).set_defaults(func=cmd_serve_panel)
    p = sub.add_parser("filter", help="optional DNS content filter (ads/malware/phishing/cryptominers)")
    _add_filter_opts(p)
    p.add_argument("--status", action="store_true", help="show what is blocked")
    p.add_argument("--update", action="store_true", help="re-download the blocklists")
    p.add_argument("--remove", action="store_true", help="remove the filter")
    p.set_defaults(func=cmd_filter)
    p = sub.add_parser("doctor", help="verify the tunnel end-to-end and repair it")
    p.add_argument("--no-heal", action="store_true", help="check only, change nothing")
    p.set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="show install status").set_defaults(func=cmd_status)
    sub.add_parser("cert", help="run certificate wizard").set_defaults(func=cmd_cert)
    p = sub.add_parser("uninstall", help="remove everything sshauto built")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--keep-users", action="store_true", help="keep tunnel user accounts")
    p.add_argument("--purge-packages", action="store_true",
                   help="also apt-purge dropbear, fail2ban, squid")
    p.set_defaults(func=cmd_uninstall)
    sub.add_parser("dashboard", help="open dashboard").set_defaults(func=cmd_dashboard)
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
