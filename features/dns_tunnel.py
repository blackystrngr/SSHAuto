"""
DNS Tunneling – uses iodine to tunnel TCP/IP over DNS queries (UDP 53).
"""
from __future__ import annotations

from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
from core.config import state

IODINE_BIN = Path("/usr/bin/iodine")
IODINE_CONFIG = Path("/etc/iodine/iodine.conf")
IODINE_SERVICE = Path("/etc/systemd/system/iodine.service")


class DnsTunnelFeature(BaseFeature):
    name = "dns_tunnel"
    description = "DNS tunneling (iodine) – UDP 53"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return IODINE_BIN.exists() and IODINE_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing iodine DNS tunnel...")

        Shell.run("apt-get install -y iodine", check=True)
        Shell.run("systemctl stop systemd-resolved 2>/dev/null", check=False)
        Shell.run("systemctl disable systemd-resolved 2>/dev/null", check=False)

        data = state.ensure_defaults()
        domain = data.get("dns_tunnel_domain", "t.yourdomain.com")
        password = data.get("dns_tunnel_password", "changeme")

        config = f"""
-p {password}
-d 0
-f
{domain}
"""
        IODINE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        IODINE_CONFIG.write_text(config)

        service = f"""
[Unit]
Description=iodine DNS tunnel
After=network.target

[Service]
ExecStart={IODINE_BIN} -c {IODINE_CONFIG}
Restart=always
User=root

[Install]
WantedBy=multi-user.target
"""
        IODINE_SERVICE.write_text(service)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable iodine", check=False)
        Shell.run("systemctl start iodine", check=False, timeout=10)

        log.success("DNS tunnel active on UDP 53.")
        log.important("Configure your domain's NS record for the tunnel domain to point to this VPS.")

    def remove(self) -> None:
        Shell.run("systemctl stop iodine", check=False)
        Shell.run("systemctl disable iodine", check=False)
        IODINE_SERVICE.unlink(missing_ok=True)
        IODINE_CONFIG.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("DNS tunnel removed.")
