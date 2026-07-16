from pathlib import Path
from core.config import DROPBEAR_BANNER_PATH, DROPBEAR_DEFAULTS_FILE, DROPBEAR_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
import time

class DropbearServiceFeature(BaseFeature):
    name = "dropbear_service"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return Path("/etc/systemd/system/dropbear-tunnel.service").exists()

    def install(self) -> None:
        data = state.ensure_defaults()
        port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        DROPBEAR_BANNER_PATH.write_text("Authorized Tunnel Access Only.\n")
        # Performance tweaks:
        # -W 1048576: larger SSH channel window (bandwidth-delay product)
        # -K 15 -I 0: keepalive every 15s, no idle timeout
        config = f"""NO_START=0
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 1048576 -K 15 -I 0"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=1048576
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        Shell.run("pkill -f dropbear", check=False)
        time.sleep(1)

        service_content = f"""[Unit]
Description=Dropbear SSH Tunnel Backend
After=network.target

[Service]
ExecStart=/usr/sbin/dropbear -F -p 127.0.0.1:{port} -W 1048576 -K 15 -I 0 -b {DROPBEAR_BANNER_PATH}
Restart=always
RestartSec=3
User=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
"""
        service_path = Path("/etc/systemd/system/dropbear-tunnel.service")
        service_path.write_text(service_content)
        log.info("Created systemd unit: dropbear-tunnel.service")

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable dropbear-tunnel", check=False, timeout=10)
        Shell.run("systemctl restart dropbear-tunnel", check=False, timeout=10)

        self._verify_binding(port)

    def remove(self) -> None:
        Shell.run("systemctl stop dropbear-tunnel", check=False, timeout=10)
        Shell.run("systemctl disable dropbear-tunnel", check=False, timeout=10)
        Path("/etc/systemd/system/dropbear-tunnel.service").unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", timeout=10)
        log.info("Dropbear removed")

    def _verify_binding(self, expected_port: int) -> None:
        for _ in range(5):
            result = Shell.run(f"ss -lpn | grep ':{expected_port}' | grep dropbear", check=False)
            if result.ok:
                log.success(f"Dropbear confirmed listening on 127.0.0.1:{expected_port}")
                return
            time.sleep(1)
        log.warning(f"Could not verify Dropbear binding on port {expected_port}.")
