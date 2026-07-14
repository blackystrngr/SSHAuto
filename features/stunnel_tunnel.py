from pathlib import Path
from core.config import state, DROPBEAR_PORT_DEFAULT
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
import time

STUNNEL_CONF = Path("/etc/stunnel/stunnel.conf")
STUNNEL_PORT = 4443
STUNNEL_CERT = Path("/etc/stunnel/stunnel.pem")


class StunnelTunnelFeature(BaseFeature):
    name = "stunnel_tunnel"
    description = f"SSL tunnel (stunnel) on 127.0.0.1:{STUNNEL_PORT} forwarding to Dropbear"
    depends_on = ["packages", "dropbear_service"]

    def is_installed(self) -> bool:
        return STUNNEL_CONF.exists() and Shell.run("systemctl is-active stunnel4", check=False).ok

    def install(self) -> None:
        log.info("Installing stunnel SSL tunnel...")

        # Check if stunnel4 is already installed
        if not Shell.run("which stunnel4", check=False).ok:
            log.info("stunnel4 not found, installing...")
            # Attempt install with retry on lock
            for attempt in range(3):
                result = Shell.run("apt-get install -y stunnel4", check=False, timeout=30)
                if result.ok:
                    break
                if "Could not get lock" in result.stderr:
                    log.warning("apt-get locked, waiting 5s...")
                    time.sleep(5)
                else:
                    raise Exception(f"Failed to install stunnel4: {result.stderr}")
            else:
                raise Exception("Failed to install stunnel4 after retries.")
        else:
            log.info("stunnel4 already installed.")

        data = state.ensure_defaults()
        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        if not STUNNEL_CERT.exists():
            log.info("Generating self‑signed certificate for stunnel...")
            Shell.run(
                f"openssl req -x509 -nodes -days 3650 -newkey rsa:2048 "
                f"-keyout {STUNNEL_CERT} -out {STUNNEL_CERT} -subj '/CN=localhost'",
                check=True
            )
            STUNNEL_CERT.chmod(0o600)

        config = f"""
pid = /var/run/stunnel.pid
debug = warning
output = /var/log/stunnel.log

[ssh-tunnel]
accept = 127.0.0.1:{STUNNEL_PORT}
connect = 127.0.0.1:{dropbear_port}
cert = {STUNNEL_CERT}
client = no
"""
        STUNNEL_CONF.write_text(config)
        log.info(f"Stunnel configured: listening on 127.0.0.1:{STUNNEL_PORT} (internal).")

        Shell.run("systemctl enable stunnel4", check=False)
        for attempt in range(3):
            result = Shell.run("systemctl restart stunnel4", check=False, timeout=10)
            if result.ok:
                break
            time.sleep(1)
        else:
            log.warning("stunnel service did not start cleanly. Check logs with 'journalctl -u stunnel4'.")

        log.success("Stunnel tunnel ready (internal).")

    def remove(self) -> None:
        Shell.run("systemctl stop stunnel4", check=False)
        Shell.run("systemctl disable stunnel4", check=False)
        STUNNEL_CONF.unlink(missing_ok=True)
        STUNNEL_CERT.unlink(missing_ok=True)
        log.info("Stunnel removed.")
