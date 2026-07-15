"""
Helper functions to restart all tunnel services.
Uses a soft restart with a timeout, and force-kills if necessary.
"""
from __future__ import annotations

import time
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

def restart_service(service: str) -> bool:
    """Restart a single service with soft stop, fallback to force kill."""
    # Check if the service exists
    check = Shell.run(f"systemctl status {service}", check=False, timeout=5)
    if "Unit" in check.stdout and "not found" in check.stdout:
        log.debug(f"{service} not installed, skipping.")
        return True

    # 1. Try a normal restart with a timeout
    restart_cmd = f"systemctl restart {service}"
    result = Shell.run(restart_cmd, check=False, timeout=15)
    if result.ok:
        log.success(f"{service} restarted.")
        return True

    # 2. If restart timed out or failed, try stop + start
    log.warning(f"{service} restart failed (exit {result.returncode}). Trying stop/start...")
    stop = Shell.run(f"systemctl stop {service}", check=False, timeout=10)
    if not stop.ok and "timed out" in stop.stderr:
        # Force kill if it's hanging
        log.warning(f"{service} stop timed out, force killing...")
        Shell.run(f"systemctl kill -s KILL {service}", check=False)
        time.sleep(1)
    # Now start
    start = Shell.run(f"systemctl start {service}", check=False, timeout=15)
    if start.ok:
        log.success(f"{service} restarted (force stop).")
        return True
    else:
        log.error(f"{service} failed to start: {start.stderr}")
        return False

def restart_all_services() -> None:
    """Restart every service in the list, log success/failure."""
    log.important("Restarting all tunnel services...")
    results = {}
    for svc in SERVICES:
        results[svc] = restart_service(svc)
    # Summary
    ok = sum(1 for v in results.values() if v)
    log.info(f"Services restarted: {ok}/{len(SERVICES)}")
    if ok < len(SERVICES):
        failed = [s for s, v in results.items() if not v]
        log.warning(f"Failed services: {', '.join(failed)}")
