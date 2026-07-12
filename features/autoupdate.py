"""
Wires up scripts/autoupdate_check.py to run every 30 seconds via a
systemd timer (chosen over a cron job for sub-minute resolution, and
over a public webhook listener because that would mean exposing another
port and validating inbound GitHub payloads for no real speed benefit —
a 30s poll already satisfies "auto update within 30 secs of a commit").
"""
from __future__ import annotations

from core.config import APP_ROOT, GIT_POLL_INTERVAL_SECONDS, LOG_DIR, SYSTEMD_DIR
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

SERVICE_NAME = "sshauto-autoupdate.service"
TIMER_NAME = "sshauto-autoupdate.timer"


class AutoUpdateFeature(BaseFeature):
    name = "autoupdate"
    description = f"Enable the {GIT_POLL_INTERVAL_SECONDS}s git auto-update timer"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        result = Shell.run(f"systemctl is-enabled {TIMER_NAME}", check=False)
        return result.ok and "enabled" in result.stdout

    def install(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        service_tpl = (APP_ROOT / "templates" / "systemd" / "sshauto-autoupdate.service.tpl").read_text()
        (SYSTEMD_DIR / SERVICE_NAME).write_text(service_tpl.format(app_root=APP_ROOT))

        timer_src = (APP_ROOT / "templates" / "systemd" / TIMER_NAME).read_text()
        (SYSTEMD_DIR / TIMER_NAME).write_text(timer_src)

        Shell.run("systemctl daemon-reload")
        Shell.run(f"systemctl enable {TIMER_NAME}")
        Shell.run(f"systemctl restart {TIMER_NAME}")
        log.success(f"auto-update timer active — new commits are picked up "
                     f"within {GIT_POLL_INTERVAL_SECONDS}s")

    def remove(self) -> None:
        Shell.run(f"systemctl disable --now {TIMER_NAME}", check=False)
        Shell.run(f"rm -f {SYSTEMD_DIR / SERVICE_NAME} {SYSTEMD_DIR / TIMER_NAME}", check=False)
        Shell.run("systemctl daemon-reload", check=False)
