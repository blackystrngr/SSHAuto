"""
Dropbear is the backend that nginx's websocket relay forwards to. It is
deliberately bound to 127.0.0.1 only — it must never be reachable
directly from the internet, only through the nginx relay on the
HTTP/HTTPS port list. That's what makes the "domain fronting" trick work:
from the outside this looks like ordinary web traffic on 443/2083/etc.
"""
from __future__ import annotations

from core.config import DROPBEAR_BANNER_PATH, DROPBEAR_DEFAULTS_FILE, DROPBEAR_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
from features.ssh_service import SSHServiceFeature


class DropbearServiceFeature(BaseFeature):
    name = "dropbear_service"
    description = "Configure Dropbear on 127.0.0.1 (backend for the nginx relay)"
    depends_on = ["packages", "ssh_service"]

    def is_installed(self) -> bool:
        if not DROPBEAR_DEFAULTS_FILE.exists():
            return False
        text = DROPBEAR_DEFAULTS_FILE.read_text()
        return "NO_START=0" in text and "127.0.0.1" in text

    def install(self) -> None:
        data = state.ensure_defaults()
        port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        # reuse the same banner ssh_service picked, dropbear just needs its
        # own copy since it can't read openssh's banner path reliably
        if SSHServiceFeature().is_installed():
            from core.config import SSH_BANNER_PATH
            DROPBEAR_BANNER_PATH.write_text(SSH_BANNER_PATH.read_text())
        else:
            DROPBEAR_BANNER_PATH.write_text("Authorized access only.\n")

        config = (
            "# Managed by sshauto - do not edit by hand, edit via the dashboard.\n"
            "NO_START=0\n"
            f'DROPBEAR_EXTRA_ARGS="-p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 65536"\n'
            'DROPBEAR_BANNER="' + str(DROPBEAR_BANNER_PATH) + '"\n'
            "DROPBEAR_RECEIVE_WINDOW=65536\n"
        )
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"dropbear bound to 127.0.0.1:{port} only (never public)")

        Shell.run("systemctl enable dropbear", check=False)
        Shell.run("systemctl restart dropbear")
        log.success("dropbear running, reachable only via the nginx relay")

    def remove(self) -> None:
        Shell.run("systemctl stop dropbear", check=False)
        Shell.run("systemctl disable dropbear", check=False)

    def set_port(self, new_port: int):
        """Used by the dashboard when the operator changes the relay port."""
        data = state.ensure_defaults()
        data["dropbear_port"] = new_port
        state.save(data)
        self.install()
        log.success(f"dropbear moved to port {new_port}")
