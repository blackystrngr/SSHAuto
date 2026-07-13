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
        if not DROPBEAR_DEFAULTS_FILE.exists():
            return False
        return "NO_START=0" in DROPBEAR_DEFAULTS_FILE.read_text()

    def install(self) -> None:
        data = state.ensure_defaults()
        port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        # Write /etc/default/dropbear
        DROPBEAR_BANNER_PATH.write_text("Authorized Tunnel Access Only.\n")
        config = f"""NO_START=0
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 65536"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=65536
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        # Systemd override with Environment=
        service_name = self._detect_service()
        if service_name:
            unit = service_name if service_name.endswith(".service") else service_name + ".service"
            override_dir = Path("/etc/systemd/system") / f"{unit}.d"
            override_dir.mkdir(parents=True, exist_ok=True)
            # Remove old overrides
            for f in override_dir.glob("*.conf"):
                f.unlink()
            (override_dir / "force-port.conf").write_text(f"[Service]\nEnvironment=DROPBEAR_PORT={port}\n")

            Shell.run("systemctl daemon-reload", timeout=10)
            Shell.run(f"systemctl enable {service_name}", check=False, timeout=10)

            # Stop, reset, then start with timeout
            Shell.run(f"systemctl stop {service_name}", check=False, timeout=10)
            Shell.run(f"systemctl reset-failed {service_name}", check=False, timeout=10)

            # Start with a timeout (10 seconds) – if it hangs, we'll fall back
            start_result = Shell.run(f"systemctl start {service_name}", check=False, timeout=10)
            if not start_result.ok:
                log.warning(f"systemctl start failed (exit {start_result.returncode}). Trying direct start.")
                # Fallback: kill any existing dropbear and start directly
                Shell.run("pkill dropbear", check=False)
                time.sleep(1)
                Shell.run(f"dropbear -p 127.0.0.1:{port} -W 65536 -b {DROPBEAR_BANNER_PATH} -E", check=False)
            else:
                log.success(f"Dropbear started via systemd on port {port}.")
        else:
            log.warning("No systemd service found; starting directly.")
            Shell.run("pkill dropbear", check=False)
            Shell.run(f"dropbear -p 127.0.0.1:{port} -W 65536 -b {DROPBEAR_BANNER_PATH} -E", check=False)

        # Verify binding
        self._verify_binding(port)

    def remove(self) -> None:
        service_name = self._detect_service()
        if service_name:
            Shell.run(f"systemctl stop {service_name}", check=False, timeout=10)
            Shell.run(f"systemctl disable {service_name}", check=False, timeout=10)
            unit = service_name if service_name.endswith(".service") else service_name + ".service"
            override_dir = Path("/etc/systemd/system") / f"{unit}.d"
            if override_dir.exists():
                for f in override_dir.glob("*.conf"):
                    f.unlink()
                try:
                    override_dir.rmdir()
                except OSError:
                    pass
            Shell.run("systemctl daemon-reload", check=False, timeout=10)
        else:
            Shell.run("pkill dropbear", check=False)
        log.info("Dropbear removed")

    def _detect_service(self) -> str | None:
        for cand in ("dropbear", "dropbear.service"):
            if Shell.run(f"systemctl status {cand}", check=False, timeout=5).ok:
                return cand
        return None

    def _verify_binding(self, expected_port: int) -> None:
        for _ in range(5):
            result = Shell.run(f"ss -lpn | grep ':{expected_port}' | grep dropbear", check=False)
            if result.ok:
                log.success(f"Dropbear confirmed listening on 127.0.0.1:{expected_port}")
                return
            time.sleep(1)
        log.warning(f"Could not verify Dropbear binding on port {expected_port}. Check with 'ss -lpn | grep dropbear'")
