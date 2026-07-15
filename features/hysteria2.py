"""
Hysteria2 – UDP/QUIC tunnel (high-performance, bypasses TCP interception).
"""
from __future__ import annotations

from pathlib import Path
from core.config import state, LETSENCRYPT_LIVE
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

HYSTERIA_BIN = Path("/usr/local/bin/hysteria")
HYSTERIA_CONFIG = Path("/etc/hysteria/config.yaml")
HYSTERIA_SERVICE = Path("/etc/systemd/system/hysteria.service")

# Direct download URL for Hysteria2 v2.10.0
HYSTERIA_URL = "https://github.com/apernet/hysteria/releases/download/app%2Fv2.10.0/hysteria-linux-amd64"


class Hysteria2Feature(BaseFeature):
    name = "hysteria2"
    description = "Install Hysteria2 UDP/QUIC tunnel"
    depends_on = ["certificates"]

    def is_installed(self) -> bool:
        return HYSTERIA_BIN.exists() and HYSTERIA_CONFIG.exists()

    def install(self) -> None:
        log.info("Installing Hysteria2...")

        # Download the binary using the provided URL
        log.info(f"Downloading Hysteria2 from: {HYSTERIA_URL}")
        result = Shell.run(f"wget -O {HYSTERIA_BIN} {HYSTERIA_URL}", check=False, timeout=60)
        if not result.ok:
            log.error(f"Failed to download Hysteria2: {result.stderr}")
            raise Exception("Hysteria2 download failed. Check network connectivity.")

        HYSTERIA_BIN.chmod(0o755)

        data = state.ensure_defaults()
        domain = data.get("cert_domain")
        if not domain:
            raise Exception("No domain set. Run 'sshauto cert' first.")

        cert = LETSENCRYPT_LIVE / domain / "fullchain.pem"
        key = LETSENCRYPT_LIVE / domain / "privkey.pem"
        if not cert.exists() or not key.exists():
            raise Exception("Certificate not found. Run 'sshauto cert' first.")

        password = data.get("hysteria_password", "helloworld")
        config = f"""
listen: :443
tls:
  cert: {cert}
  key: {key}
auth:
  type: password
  password: "{password}"
masquerade:
  type: proxy
  proxy:
    url: https://{domain}
    rewriteHost: true
"""
        HYSTERIA_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        HYSTERIA_CONFIG.write_text(config)

        service = f"""
[Unit]
Description=Hysteria2 UDP/QUIC Tunnel
After=network.target

[Service]
ExecStart={HYSTERIA_BIN} server -c {HYSTERIA_CONFIG}
Restart=always
User=root

[Install]
WantedBy=multi-user.target
"""
        HYSTERIA_SERVICE.write_text(service)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable hysteria", check=False)
        Shell.run("systemctl start hysteria", check=False, timeout=10)

        log.success("Hysteria2 installed and running on UDP 443.")

    def remove(self) -> None:
        Shell.run("systemctl stop hysteria", check=False)
        Shell.run("systemctl disable hysteria", check=False)
        HYSTERIA_BIN.unlink(missing_ok=True)
        HYSTERIA_CONFIG.unlink(missing_ok=True)
        HYSTERIA_SERVICE.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Hysteria2 removed.")
