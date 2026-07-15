"""
Adds a cron job to restart all tunnel-related services daily.
"""
from __future__ import annotations

from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

CRON_SCRIPT = Path("/usr/local/bin/sshauto-restart.sh")
CRON_FILE = Path("/etc/cron.d/sshauto-restart")


class CronRestartFeature(BaseFeature):
    name = "cron_restart"
    description = "Add daily cron job to restart all tunnel services"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return CRON_FILE.exists()

    def install(self) -> None:
        log.info("Adding cron job to restart services daily at 3:00 AM...")

        script_content = """#!/usr/bin/env bash
# Restart all tunnel services
systemctl restart nginx 2>/dev/null || true
systemctl restart squid 2>/dev/null || true
systemctl restart dropbear-tunnel 2>/dev/null || true
systemctl restart ws-ssh-proxy 2>/dev/null || true
systemctl restart badvpn-udpgw 2>/dev/null || true
systemctl restart stunnel4 2>/dev/null || true
systemctl restart sslh 2>/dev/null || true
systemctl restart haproxy 2>/dev/null || true
# Log the restart
echo "$(date): All services restarted" >> /var/log/sshauto/restart.log
"""
        CRON_SCRIPT.write_text(script_content)
        CRON_SCRIPT.chmod(0o755)

        cron_line = "0 3 * * * root /usr/local/bin/sshauto-restart.sh > /dev/null 2>&1\n"
        CRON_FILE.write_text(cron_line)

        Shell.run("systemctl reload cron", check=False)
        log.success("Cron job installed – services will restart daily at 3:00 AM.")
        log.important("Logs written to /var/log/sshauto/restart.log")

    def remove(self) -> None:
        CRON_SCRIPT.unlink(missing_ok=True)
        CRON_FILE.unlink(missing_ok=True)
        Shell.run("systemctl reload cron", check=False)
        log.info("Cron job removed.")
