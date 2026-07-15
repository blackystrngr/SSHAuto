"""
Hysteria2 – UDP/QUIC tunnel using the official install script.
Fully configurable via state (port, domain, password).
"""
from __future__ import annotations

from pathlib import Path
from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

HYSTERIA_CONFIG = Path("/etc/hysteria/config.yaml")
HYSTERIA_SERVICE = Path("/etc/systemd/system/hysteria-server.service")


class Hysteria2Feature(BaseFeature):
    name = "hysteria2"
    description = "Install Hysteria2 UDP/QUIC tunnel (official script)"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return HYSTERIA_CONFIG.exists() and HYSTERIA_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing Hysteria2 using official script...")

        data = state.ensure_defaults()
        port = data.get("hysteria_port", 2096)
        domain = data.get("hysteria_domain", "online.mobitel.lk")
        password = data.get("hysteria_password", "helloworld")

        # 1. Run official install script
        log.info("Downloading and running get.hy2.sh...")
        result = Shell.run('bash <(curl -fsSL https://get.hy2.sh/)', check=False, timeout=120)
        if not result.ok:
            log.error(f"Official install failed: {result.stderr}")
            raise Exception("Hysteria2 install script failed.")

        # 2. Generate self‑signed certificate for the configured domain
        log.info(f"Generating self‑signed certificate for {domain}...")
        cert_dir = Path("/etc/hysteria")
        cert_dir.mkdir(parents=True, exist_ok=True)
        Shell.run(
            f"openssl req -x509 -nodes -newkey rsa:2048 "
            f"-keyout {cert_dir}/server.key -out {cert_dir}/server.crt "
            f"-days 3650 -subj '/CN={domain}'",
            check=True,
            timeout=30
        )

        # 3. Write config
        config = f"""
listen: :{port}
tls:
  cert: {cert_dir}/server.crt
  key: {cert_dir}/server.key
auth:
  type: password
  password: "{password}"
masquerade:
  type: proxy
  proxy:
    url: https://{domain}
    rewriteHost: true
"""
        HYSTERIA_CONFIG.write_text(config)
        log.info(f"Hysteria2 config written (port {port}, domain {domain})")

        # 4. Open firewall (ufw)
        Shell.run(f"ufw allow {port}/udp", check=False, timeout=10)

        # 5. Enable and start the service
        Shell.run("systemctl daemon-reload", check=False)
        Shell.run("systemctl enable hysteria-server", check=False)
        Shell.run("systemctl restart hysteria-server", check=False)

        status = Shell.run("systemctl is-active hysteria-server", check=False, timeout=5)
        if status.ok and "active" in status.stdout:
            log.success(f"Hysteria2 installed and running on UDP {port}.")
        else:
            log.warning("Hysteria2 service may not be active. Check 'systemctl status hysteria-server'")

        log.important("Client configuration:")
        log.important(f"  Server: your_vps_ip:{port}")
        log.important(f"  Password: {password}")
        log.important(f"  SNI: {domain}")
        log.important("  Allow Insecure: YES (self‑signed cert)")

    def remove(self) -> None:
        Shell.run("systemctl stop hysteria-server", check=False)
        Shell.run("systemctl disable hysteria-server", check=False)
        port = state.get("hysteria_port", 2096)
        Shell.run(f"ufw delete allow {port}/udp", check=False)
        HYSTERIA_CONFIG.unlink(missing_ok=True)
        Path("/etc/hysteria/server.crt").unlink(missing_ok=True)
        Path("/etc/hysteria/server.key").unlink(missing_ok=True)
        log.info("Hysteria2 removed (binary remains).")
