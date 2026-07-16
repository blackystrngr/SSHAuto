"""
Helper functions to restart all tunnel services (only if they exist).
"""
from __future__ import annotations

import time
from core.logger import log
from core.shell import Shell

# List of services we might want to restart – but we'll check existence first.
SERVICES = [
    "nginx",
    "dropbear-tunnel",
    "ws-ssh-proxy",
    "badvpn-udpgw",
    "sslh",
    "hysteria-server",
    "dnstt-server",
    "pingtunnel",
]


def service_exists(service: str) -> bool:
    """Check if a systemd service exists."""
    check = Shell.run(f"systemctl cat {service}", check=False, timeout=5)
    return check.ok  # if 'cat' returns 0, the unit exists


def restart_service(service: str) -> bool:
    """Restart a single service only if it exists."""
    if not service_exists(service):
        log.debug(f"{service} not installed, skipping.")
        return True

    result = Shell.run(f"systemctl restart {service}", check=False, timeout=15)
    if result.ok:
        log.success(f"{service} restarted.")
        return True

    log.warning(f"{service} restart failed (exit {result.returncode}). Trying stop/start...")
    stop = Shell.run(f"systemctl stop {service}", check=False, timeout=10)
    if not stop.ok and "timed out" in stop.stderr:
        log.warning(f"{service} stop timed out, force killing...")
        Shell.run(f"systemctl kill -s KILL {service}", check=False)
        time.sleep(1)
    start = Shell.run(f"systemctl start {service}", check=False, timeout=15)
    if start.ok:
        log.success(f"{service} restarted (force stop).")
        return True
    else:
        log.error(f"{service} failed to start: {start.stderr}")
        return False


def restart_all_services() -> None:
    log.important("Restarting existing services...")
    results = {}
    for svc in SERVICES:
        results[svc] = restart_service(svc)
    ok = sum(1 for v in results.values() if v)
    total = sum(1 for v in results.values() if v is not None)  # we count all attempts
    log.info(f"Services restarted: {ok}/{len(SERVICES)}")
    if ok < len(SERVICES):
        failed = [s for s, v in results.items() if not v and v is not None]
        if failed:
            log.warning(f"Failed services: {', '.join(failed)}")
