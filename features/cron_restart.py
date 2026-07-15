"""
Adds a cron job to restart all tunnel-related services daily.
This prevents long-term service hangs and ensures stable operation.
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

        # Create the restart script
        script_content = """#!/usr/bin/env bash
# Restart all tunnel services
systemctl restart nginx
systemctl restart squid
systemctl restart dropbear-tunnel
systemctl restart ws-ssh-proxy
systemctl restart badvpn-udpgw
systemctl restart stunnel4
systemctl restart sslh
# Log the restart
echo "$(date): All services restarted" >> /var/log/sshauto/restart.log
"""
        CRON_SCRIPT.write_text(script_content)
        CRON_SCRIPT.chmod(0o755)

        # Create cron job (daily at 3:00 AM)
        cron_line = "0 3 * * * root /usr/local/bin/sshauto-restart.sh > /dev/null 2>&1\n"
        CRON_FILE.write_text(cron_line)

        # Ensure cron daemon reloads (it watches /etc/cron.d automatically)
        Shell.run("systemctl reload cron", check=False)

        log.success("Cron job installed – services will restart daily at 3:00 AM.")
        log.important("Logs written to /var/log/sshauto/restart.log")

    def remove(self) -> None:
        CRON_SCRIPT.unlink(missing_ok=True)
        CRON_FILE.unlink(missing_ok=True)
        Shell.run("systemctl reload cron", check=False)
        log.info("Cron job removed.")
