import time
from pathlib import Path

from core.config import DROPBEAR_BANNER_PATH, DROPBEAR_DEFAULTS_FILE, DROPBEAR_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


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
        # The tunnel runs from dropbear-tunnel.service (loopback only). The distro's own
        # dropbear service must stay off, otherwise it grabs the port on 0.0.0.0 first.
        config = f"""NO_START=1
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-b {DROPBEAR_BANNER_PATH} -W 524288 -K 15 -I 0"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=524288
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        for unit in ("dropbear.socket", "dropbear"):
            Shell.run(f"systemctl disable --now {unit}", check=False, timeout=15)
        Shell.run("systemctl mask dropbear", check=False, timeout=10)
        Shell.run("pkill -f dropbear", check=False)
        time.sleep(1)

        # ONLY bind to 127.0.0.1 – no public access
        service_content = f"""[Unit]
Description=Dropbear SSH Tunnel Backend
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/sbin/dropbear -F -p 127.0.0.1:{port} -W 524288 -K 15 -I 0 -b {DROPBEAR_BANNER_PATH}
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
        log.info("Created systemd unit (Dropbear bound only to localhost).")

        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        Shell.run("systemctl enable dropbear-tunnel", check=False, timeout=10)
        Shell.run("systemctl restart dropbear-tunnel", check=False, timeout=10)

        self._verify_binding(port)

    def remove(self) -> None:
        Shell.run("systemctl stop dropbear-tunnel", check=False, timeout=10)
        Shell.run("systemctl disable dropbear-tunnel", check=False, timeout=10)
        Path("/etc/systemd/system/dropbear-tunnel.service").unlink(missing_ok=True)
        Shell.run("systemctl unmask dropbear", check=False, timeout=10)
        DROPBEAR_BANNER_PATH.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        log.info("Dropbear removed")

    def _verify_binding(self, expected_port: int) -> None:
        for _ in range(5):
            result = Shell.run(f"ss -lpn | grep ':{expected_port}' | grep dropbear", check=False)
            if result.ok:
                log.success(f"Dropbear confirmed listening on 127.0.0.1:{expected_port}")
                return
            time.sleep(1)
        log.warning(f"Could not verify Dropbear binding on port {expected_port}.")
