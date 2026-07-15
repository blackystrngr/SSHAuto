"""
DNS Tunneling using the dnstt-deploy script (bugfloyd).
Configurable via state (domain, password, mode).
"""
from __future__ import annotations

from pathlib import Path
from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

DNSTT_CONFIG = Path("/etc/dnstt/config")
DNSTT_SERVICE = Path("/etc/systemd/system/dnstt-server.service")


class DnsTunnelFeature(BaseFeature):
    name = "dns_tunnel"
    description = "DNS tunneling using bugfloyd/dnstt-deploy"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return DNSTT_CONFIG.exists() and DNSTT_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing DNS tunnel using dnstt-deploy script...")

        data = state.ensure_defaults()
        domain = data.get("dns_tunnel_domain", "t.yourdomain.com")
        password = data.get("dns_tunnel_password", "helloworld")
        mode = data.get("dns_tunnel_mode", "socks")

        log.info(f"Installing with domain: {domain}, mode: {mode}")

        # Try non‑interactive mode
        cmd = f"bash <(curl -Ls https://raw.githubusercontent.com/bugfloyd/dnstt-deploy/main/dnstt-deploy.sh) -d {domain} -p {password} -m {mode}"
        result = Shell.run(cmd, check=False, timeout=180)

        if not result.ok:
            log.warning("Automated install failed; falling back to interactive mode.")
            log.important("Please run the script manually:")
            log.important(f"  bash <(curl -Ls https://raw.githubusercontent.com/bugfloyd/dnstt-deploy/main/dnstt-deploy.sh)")
            log.important(f"  Choose option 1, enter {domain}, select {mode} mode, MTU 1232.")
            DNSTT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            DNSTT_CONFIG.write_text(f"# Placeholder config for {domain}")
            return

        Shell.run("systemctl daemon-reload", check=False)
        Shell.run("systemctl enable dnstt-server", check=False)
        Shell.run("systemctl start dnstt-server", check=False)

        pubkey_file = Path("/etc/dnstt/public.key")
        if pubkey_file.exists():
            pubkey = pubkey_file.read_text().strip()
            log.success("DNS tunnel installed.")
            log.important(f"Your DNSTT Public Key: {pubkey}")
        else:
            log.warning("Public key not found; check /etc/dnstt/")

        log.important("Client configuration:")
        log.important(f"  Tunnel Domain: {domain}")
        log.important(f"  Public Key: (copy from above)")
        log.important(f"  Mode: {mode}")

    def remove(self) -> None:
        Shell.run("systemctl stop dnstt-server", check=False)
        Shell.run("systemctl disable dnstt-server", check=False)
        DNSTT_CONFIG.unlink(missing_ok=True)
        DNSTT_SERVICE.unlink(missing_ok=True)
        Path("/etc/dnstt").unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("DNS tunnel removed.")
