"""
DNS Tunneling – compiles dnstt from source, uses generated keys.
"""
from __future__ import annotations

from pathlib import Path
from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

DNSTT_BIN = Path("/usr/local/bin/dnstt-server")
DNSTT_CONFIG_DIR = Path("/etc/dnstt")
DNSTT_PUB_KEY = DNSTT_CONFIG_DIR / "server.pub"
DNSTT_PRIV_KEY = DNSTT_CONFIG_DIR / "server.key"
DNSTT_SERVICE = Path("/etc/systemd/system/dnstt-server.service")


class DnsTunnelFeature(BaseFeature):
    name = "dns_tunnel"
    description = "DNS tunneling (dnstt) – compiled from source"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return DNSTT_BIN.exists() and DNSTT_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing dnstt DNS tunnel from source...")

        data = state.ensure_defaults()
        domain = data.get("dns_tunnel_domain", "ns1.hi.blackstrngr.qzz.io")
        dns_port = data.get("dns_tunnel_port", 5300)
        target = "127.0.0.1:22"  # default target

        # 1. Install Go if not present
        Shell.run("apt-get install -y golang-go git", check=True)

        # 2. Clone and build dnstt
        log.info("Cloning dnstt source...")
        Shell.run("rm -rf /tmp/dnstt", check=False)
        Shell.run("git clone https://www.bamsoftware.com/git/dnstt.git /tmp/dnstt", check=True)
        Shell.run("cd /tmp/dnstt/dnstt-server && go build", check=True)
        Shell.run("cp /tmp/dnstt/dnstt-server/dnstt-server /usr/local/bin/", check=True)
        Shell.run("chmod +x /usr/local/bin/dnstt-server", check=True)
        Shell.run("rm -rf /tmp/dnstt", check=False)

        # 3. Generate DNSTT cryptographic keys
        DNSTT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Generating DNSTT keys...")
        Shell.run(
            f"cd {DNSTT_CONFIG_DIR} && {DNSTT_BIN} -gen-key -pubkey {DNSTT_PUB_KEY} -privkey {DNSTT_PRIV_KEY}",
            check=True
        )

        # 4. Create systemd service
        service_content = f"""
[Unit]
Description=DNSTT DNS Tunnel Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={DNSTT_CONFIG_DIR}
ExecStart={DNSTT_BIN} -udp :{dns_port} -privkey-file {DNSTT_PRIV_KEY} {target}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        DNSTT_SERVICE.write_text(service_content)
        log.info(f"Service created with UDP port {dns_port}")

        # 5. Open firewall (UDP)
        Shell.run(f"ufw allow {dns_port}/udp", check=False, timeout=10)

        # 6. Enable and start
        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable dnstt-server", check=False)
        Shell.run("systemctl start dnstt-server", check=False, timeout=10)

        # 7. Show public key (for clients)
        if DNSTT_PUB_KEY.exists():
            pubkey = DNSTT_PUB_KEY.read_text().strip()
            log.success("DNSTT installed.")
            log.important(f"Your DNSTT Public Key:\n{pubkey}")
            log.important(f"Tunnel Domain: {domain}")
            log.important(f"UDP port: {dns_port}")
        else:
            log.warning("Public key not found.")

    def remove(self) -> None:
        Shell.run("systemctl stop dnstt-server", check=False)
        Shell.run("systemctl disable dnstt-server", check=False)
        DNSTT_SERVICE.unlink(missing_ok=True)
        DNSTT_CONFIG_DIR.unlink(missing_ok=True)
        DNSTT_BIN.unlink(missing_ok=True)
        port = state.get("dns_tunnel_port", 5300)
        Shell.run(f"ufw delete allow {port}/udp", check=False)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("DNSTT removed.")
