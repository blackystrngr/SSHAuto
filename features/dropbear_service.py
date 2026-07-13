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
        # Check if our custom unit exists and is enabled
        service_path = Path("/etc/systemd/system/sshauto-dropbear.service")
        return service_path.exists() and Shell.run("systemctl is-enabled sshauto-dropbear", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        # Write /etc/default/dropbear (for compatibility)
        DROPBEAR_BANNER_PATH.write_text("Authorized Tunnel Access Only.\n")
        config = f"""NO_START=0
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 65536"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=65536
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        # Determine dropbear binary path
        which = Shell.run("which dropbear", check=False)
        dropbear_bin = which.stdout.strip() if which.ok else "/usr/sbin/dropbear"

        # Write custom systemd unit
        service_content = f"""[Unit]
Description=SSHAuto Dropbear Tunnel Backend
After=network.target

[Service]
ExecStart={dropbear_bin} -EF -p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 65536
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
"""
        service_path = Path("/etc/systemd/system/sshauto-dropbear.service")
        service_path.write_text(service_content)

        # Disable any existing dropbear service to avoid conflict
        for old in ("dropbear", "dropbear.service"):
            if Shell.run(f"systemctl is-enabled {old}", check=False).ok:
                Shell.run(f"systemctl disable {old}", check=False)
                Shell.run(f"systemctl stop {old}", check=False)

        # Enable and start our custom unit
        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable sshauto-dropbear")
        Shell.run("systemctl restart sshauto-dropbear")

        # Verify
        if not Shell.run("systemctl is-active sshauto-dropbear", check=False).ok:
            log.warning("Dropbear service failed to start; falling back to direct start.")
            Shell.run("pkill dropbear", check=False)
            # Run in background (using nohup)
            Shell.run(f"nohup {dropbear_bin} -p 127.0.0.1:{port} -W 65536 -b {DROPBEAR_BANNER_PATH} -E > /dev/null 2>&1 &", check=False)
        else:
            log.success(f"Dropbear started via custom systemd unit on port {port}.")

        self._verify_binding(port)

    def remove(self) -> None:
        service_path = Path("/etc/systemd/system/sshauto-dropbear.service")
        if service_path.exists():
            Shell.run("systemctl stop sshauto-dropbear", check=False)
            Shell.run("systemctl disable sshauto-dropbear", check=False)
            service_path.unlink()
            Shell.run("systemctl daemon-reload", check=False)
        else:
            Shell.run("pkill dropbear", check=False)
        log.info("Dropbear removed")

    def _verify_binding(self, expected_port: int) -> None:
        for _ in range(5):
            result = Shell.run(f"ss -lpn | grep ':{expected_port}' | grep dropbear", check=False)
            if result.ok:
                log.success(f"Dropbear confirmed listening on 127.0.0.1:{expected_port}")
                return
            time.sleep(1)
        log.warning(f"Could not verify Dropbear binding on port {expected_port}. Check with 'ss -lpn | grep dropbear'")
