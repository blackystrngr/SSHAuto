from pathlib import Path
from core.config import DROPBEAR_BANNER_PATH, DROPBEAR_DEFAULTS_FILE, DROPBEAR_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

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

        # Write /etc/default/dropbear (exactly like script)
        DROPBEAR_BANNER_PATH.write_text("Authorized Tunnel Access Only.\n")
        config = f"""NO_START=0
DROPBEAR_PORT={port}
DROPBEAR_EXTRA_ARGS="-p 127.0.0.1:{port} -b {DROPBEAR_BANNER_PATH} -W 65536 -c chacha20-poly1305@openssh.com,aes128-gcm@openssh.com -m umac-128-etm@openssh.com"
DROPBEAR_BANNER="{DROPBEAR_BANNER_PATH}"
DROPBEAR_RECEIVE_WINDOW=65536
"""
        DROPBEAR_DEFAULTS_FILE.write_text(config)
        log.info(f"Dropbear defaults written (port {port})")

        # Systemd override with Environment= (script's method)
        service_name = self._detect_service()
        if service_name:
            unit = service_name if service_name.endswith(".service") else service_name + ".service"
            override_dir = Path("/etc/systemd/system") / f"{unit}.d"
            override_dir.mkdir(parents=True, exist_ok=True)
            # Remove old overrides
            for f in override_dir.glob("*.conf"):
                f.unlink()
            # Write new override
            (override_dir / "force-port.conf").write_text(f"[Service]\nEnvironment=DROPBEAR_PORT={port}\n")

            Shell.run("systemctl daemon-reload")
            Shell.run(f"systemctl enable {service_name}", check=False)
            Shell.run(f"systemctl stop {service_name}", check=False)
            Shell.run(f"systemctl reset-failed {service_name}", check=False)
            Shell.run(f"systemctl start {service_name}", check=False)
            log.success(f"Dropbear started on port {port} (systemd override).")
        else:
            # Fallback: start directly
            Shell.run("pkill dropbear", check=False)
            Shell.run(f"dropbear -p 127.0.0.1:{port} -W 65536 -b {DROPBEAR_BANNER_PATH} -E", check=False)
            log.success(f"Dropbear started directly on port {port}.")

    def remove(self) -> None:
        service_name = self._detect_service()
        if service_name:
            Shell.run(f"systemctl stop {service_name}", check=False)
            Shell.run(f"systemctl disable {service_name}", check=False)
            unit = service_name if service_name.endswith(".service") else service_name + ".service"
            override_dir = Path("/etc/systemd/system") / f"{unit}.d"
            if override_dir.exists():
                for f in override_dir.glob("*.conf"):
                    f.unlink()
                try:
                    override_dir.rmdir()
                except OSError:
                    pass
            Shell.run("systemctl daemon-reload", check=False)
        else:
            Shell.run("pkill dropbear", check=False)
        log.info("Dropbear removed")

    def _detect_service(self) -> str | None:
        for cand in ("dropbear", "dropbear.service"):
            if Shell.run(f"systemctl status {cand}", check=False).ok:
                return cand
        return None
