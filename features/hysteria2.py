"""
Hysteria2 – UDP/QUIC tunnel (high-performance, bypasses TCP interception).
"""
from __future__ import annotations

from pathlib import Path
from core.config import state, LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

HYSTERIA_BIN = Path("/usr/local/bin/hysteria")
HYSTERIA_CONFIG = Path("/etc/hysteria/config.yaml")
HYSTERIA_SERVICE = Path("/etc/systemd/system/hysteria.service")

HYSTERIA_URL = "https://github.com/apernet/hysteria/releases/download/app%2Fv2.10.0/hysteria-linux-amd64"


class Hysteria2Feature(BaseFeature):
    name = "hysteria2"
    description = "Install Hysteria2 UDP/QUIC tunnel"
    depends_on = ["certificates"]

    def is_installed(self) -> bool:
        return HYSTERIA_BIN.exists() and HYSTERIA_CONFIG.exists()

    def install(self) -> None:
        log.info("Installing Hysteria2...")

        # Download binary
        log.info(f"Downloading Hysteria2 from: {HYSTERIA_URL}")
        result = Shell.run(f"wget -O {HYSTERIA_BIN} {HYSTERIA_URL}", check=False, timeout=60)
        if not result.ok:
            log.error(f"Failed to download Hysteria2: {result.stderr}")
            raise Exception("Hysteria2 download failed.")
        HYSTERIA_BIN.chmod(0o755)

        # Resolve certificate paths (same logic as nginx_relay)
        data = state.ensure_defaults()
        domain = data.get("cert_domain")
        if not domain:
            raise Exception("No domain set. Run 'sshauto cert' first.")

        cert_path, key_path = self._resolve_cert_paths(data, domain)
        if not cert_path or not key_path:
            raise Exception("Certificate not found. Run 'sshauto cert' first.")

        password = data.get("hysteria_password", "helloworld")

        config = f"""
listen: :443
tls:
  cert: {cert_path}
  key: {key_path}
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

    def _resolve_cert_paths(self, data: dict, domain: str):
        # 1. Check state first
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        if cert and key and Path(cert).exists() and Path(key).exists():
            return cert, key

        # 2. Check Let's Encrypt
        le_cert = LETSENCRYPT_LIVE / domain / "fullchain.pem"
        le_key = LETSENCRYPT_LIVE / domain / "privkey.pem"
        if le_cert.exists() and le_key.exists():
            data["cert_fullchain_path"] = str(le_cert)
            data["cert_key_path"] = str(le_key)
            state.save(data)
            return str(le_cert), str(le_key)

        # 3. Check self-signed (from nginx/sshauto)
        ss_cert = SSHAUTO_CERT_DIR / domain / "fullchain.pem"
        ss_key = SSHAUTO_CERT_DIR / domain / "privkey.pem"
        if ss_cert.exists() and ss_key.exists():
            data["cert_fullchain_path"] = str(ss_cert)
            data["cert_key_path"] = str(ss_key)
            state.save(data)
            return str(ss_cert), str(ss_key)

        # 4. Check the common /etc/ssl/certs/selfsigned.crt
        script_cert = Path("/etc/ssl/certs/selfsigned.crt")
        script_key = Path("/etc/ssl/private/selfsigned.key")
        if script_cert.exists() and script_key.exists():
            data["cert_fullchain_path"] = str(script_cert)
            data["cert_key_path"] = str(script_key)
            state.save(data)
            return str(script_cert), str(script_key)

        # 5. Check any other locations (like /var/lib/sshauto/certs/*)
        for base_dir in [LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, Path("/etc/ssl/cloudflare")]:
            if not base_dir.exists():
                continue
            for cert_file in base_dir.glob("*/fullchain.pem"):
                key_file = cert_file.parent / "privkey.pem"
                if key_file.exists():
                    data["cert_fullchain_path"] = str(cert_file)
                    data["cert_key_path"] = str(key_file)
                    state.save(data)
                    return str(cert_file), str(key_file)

        return None, None
