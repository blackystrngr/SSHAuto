"""
Helper functions to restart all tunnel services.
"""
from __future__ import annotations

from core.logger import log
from core.shell import Shell

# List of services managed by sshauto
SERVICES = [
    "nginx",
    "squid",
    "dropbear-tunnel",
    "ws-ssh-proxy",
    "badvpn-udpgw",
    "stunnel4",
    "sslh",
    "haproxy",   # optional
]


def restart_all_services() -> None:
    """Restart every service in the list, log success/failure."""
    log.important("Restarting all tunnel services...")
    for svc in SERVICES:
        # Check if service exists (systemctl status)
        exists = Shell.run(f"systemctl status {svc}", check=False)
        if not exists.ok and "Unit" in exists.stdout and "not found" in exists.stdout:
            log.debug(f"{svc} not installed, skipping.")
            continue
        # Restart with timeout
        result = Shell.run(f"systemctl restart {svc}", check=False, timeout=30)
        if result.ok:
            log.success(f"{svc} restarted.")
        else:
            log.warning(f"{svc} restart failed (exit {result.returncode}).")
