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

        # Generate host keys
        key_dir = Path("/etc/dropbear")
        key_dir.mkdir(parents=True, exist_ok=True)
        for key_type in ("rsa", "ecdsa", "ed25519"):
            key_file = key_dir / f"dropbear_{key_type}_host_key"
            if not key_file.exists():
                Shell.run(f"dropbearkey -t {key_type} -f {key_file}", check=False)

        DROPBEAR_BANNER_PATH.write_text("Authorized Tunnel Access Only.\n")
        config = f"""NO_START=0
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-b {DROPBEAR_BANNER_PATH} -W 524288 -K 15 -I 0"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=524288
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        Shell.run("pkill -f dropbear", check=False)
        time.sleep(1)

        # Bind to both 127.0.0.1:110 (for proxy) AND 0.0.0.0:2222 (for direct SSH)
        public_ssh_port = data.get("public_ssh_port", 2222)
        service_content = f"""[Unit]
Description=Dropbear SSH Tunnel Backend
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/sbin/dropbear -F -p 127.0.0.1:{port} -p 0.0.0.0:{public_ssh_port} -W 524288 -K 15 -I 0 -b {DROPBEAR_BANNER_PATH}
Restart=always
RestartSec=1
User=root
LimitNOFILE=65536
Nice=-10
IOSchedulingClass=best-effort
IOSchedulingPriority=0

[Install]
WantedBy=multi-user.target
"""
        service_path = Path("/etc/systemd/system/dropbear-tunnel.service")
        service_path.write_text(service_content)
        log.info(f"Created systemd unit: dropbear-tunnel.service (listening on {public_ssh_port} for direct SSH)")

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable dropbear-tunnel", check=False, timeout=10)
        Shell.run("systemctl restart dropbear-tunnel", check=False, timeout=10)

        self._verify_binding(port)
        log.important(f"Direct SSH is now available on port {public_ssh_port} (use ssh -p {public_ssh_port} user@server)")

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
