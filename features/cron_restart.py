"""
Adds a cron job to restart all tunnel-related services daily.
Uses timeout to prevent hanging.
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
# Restart all tunnel services with a 15-second timeout per service
restart_service() {
    service=$1
    echo "Restarting $service..."
    timeout 15 systemctl restart $service 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "$service restart timed out, force stopping..."
        timeout 5 systemctl stop $service 2>/dev/null
        timeout 2 systemctl kill -s KILL $service 2>/dev/null
        sleep 1
        systemctl start $service 2>/dev/null
    fi
}

restart_service nginx
restart_service squid
restart_service dropbear-tunnel
restart_service ws-ssh-proxy
restart_service badvpn-udpgw
restart_service stunnel4
restart_service sslh

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
