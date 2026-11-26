"""Remove everything sshauto built on this machine."""
from __future__ import annotations

import shutil
from pathlib import Path

from core.config import APP_ROOT, LOG_DIR, USER_GROUP
from core.logger import log
from core.plugin_manager import PluginManager
from core.shell import Shell

STATE_DIR = Path("/var/lib/sshauto")
KK_LAUNCHER = Path("/usr/local/bin/kk")
INSTALL_DIR = Path("/opt/sshauto")
LEGACY_UNITS = ("ws-ssh-proxy", "sshauto-proxy", "dropbear-tunnel", "badvpn-udpgw", "sshauto-dnsfilter")
PURGE_PACKAGES = ["nginx", "dropbear", "fail2ban", "squid"]

PLAN = """This will remove everything sshauto created:
  - tunnel users (group '{group}') and their home directories{users_note}
  - services: ws-ssh-proxy, dropbear-tunnel, badvpn-udpgw, optional Squid and DNS content filter
  - nginx relay site, fail2ban jails, OpenSSH hardening + banner (SSH port is left as is)
  - certificates, state ({state}), logs ({logs}), the 'kk' command{app_note}
  - custom relay port firewall rules (base ports stay open: other services may use them)
{pkg_note}"""


def _tunnel_users() -> list[str]:
    res = Shell.run(f"getent group {USER_GROUP}", check=False)
    if not res.ok:
        return []
    members = res.stdout.strip().split(":")[-1]
    return [u for u in members.split(",") if u]


def _remove_users() -> None:
    users = _tunnel_users()
    for user in users:
        Shell.run(f"pkill -KILL -u {user}", check=False)
        if Shell.run(f"userdel -rf {user}", check=False).ok:
            log.success(f"deleted user '{user}'")
        else:
            log.warning(f"could not delete user '{user}'")
    Shell.run(f"groupdel {USER_GROUP}", check=False)


def run(*, yes: bool = False, purge_packages: bool = False, keep_users: bool = False) -> bool:
    """Tear everything down. Returns False if the user aborted."""
    app_removable = APP_ROOT.resolve() == INSTALL_DIR
    print(PLAN.format(
        group=USER_GROUP, state=STATE_DIR, logs=LOG_DIR,
        users_note=" (KEPT: --keep-users)" if keep_users else f" ({len(_tunnel_users())} found)",
        app_note=f", and {INSTALL_DIR}" if app_removable else "",
        pkg_note=("  - APT PACKAGES PURGED: " + ", ".join(PURGE_PACKAGES) +
                  "  (nginx/squid may be used by other apps!)") if purge_packages else "",
    ))
    if not yes and input("Type 'yes' to continue: ").strip().lower() != "yes":
        log.warning("aborted, nothing changed")
        return False

    log.rule("uninstall")
    if not keep_users:
        _remove_users()

    manager = PluginManager()
    for feature in reversed(manager._ordered()):
        if feature.name == "packages":   # handled by --purge-packages
            continue
        try:
            feature.remove()
            log.success(f"{feature.name} removed")
        except Exception as exc:
            log.error(f"{feature.name} teardown failed: {exc}")

    # anything the features may have missed
    for unit in LEGACY_UNITS:
        Shell.run(f"systemctl disable --now {unit}", check=False, timeout=20)
        Path(f"/etc/systemd/system/{unit}.service").unlink(missing_ok=True)
    for leftover in ("/usr/local/bin/ws_ssh_proxy.py", "/usr/local/bin/ws_proxy.py",
                     "/usr/local/bin/badvpn-udpgw"):
        Path(leftover).unlink(missing_ok=True)
    shutil.rmtree("/tmp/badvpn_src", ignore_errors=True)
    shutil.rmtree("/etc/sshauto", ignore_errors=True)
    for unit in ("dropbear-tunnel", "ws-ssh-proxy"):
        shutil.rmtree(f"/etc/systemd/system/{unit}.service.d", ignore_errors=True)
    Shell.run("systemctl daemon-reload", check=False, timeout=10)
    Shell.run("systemctl reset-failed", check=False, timeout=10)

    if purge_packages:
        log.important("Purging packages: " + ", ".join(PURGE_PACKAGES))
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get purge -y " + " ".join(PURGE_PACKAGES),
                  check=False, timeout=300)
        Shell.run("apt-get autoremove -y", check=False, timeout=300)

    KK_LAUNCHER.unlink(missing_ok=True)
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    shutil.rmtree(LOG_DIR, ignore_errors=True)

    log.rule("done")
    log.success("sshauto removed.")
    if app_removable:
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    else:
        log.info(f"Source directory left in place: {APP_ROOT} (delete it manually).")
    return True
