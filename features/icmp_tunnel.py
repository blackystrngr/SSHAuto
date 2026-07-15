"""
ICMP Tunneling – uses ICMPTunnel from Qteam-official to tunnel TCP over ICMP (ping) packets.
"""
from __future__ import annotations

import json
from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
from core.config import state

ICMPTUNNEL_BIN = Path("/usr/local/bin/ICMPTunnel")
ICMPTUNNEL_CONFIG = Path("/etc/icmptunnel/config.json")
ICMPTUNNEL_SERVICE = Path("/etc/systemd/system/icmptunnel.service")

# Direct download URL for ICMPTunnel v1.1.0
ICMPTUNNEL_URL = "https://github.com/Qteam-official/ICMPTunnel/releases/download/v1.1.0/ICMPTunnel-linux-amd64"


class IcmpTunnelFeature(BaseFeature):
    name = "icmp_tunnel"
    description = "ICMP tunneling (ICMPTunnel) – uses ping packets"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return ICMPTUNNEL_BIN.exists() and ICMPTUNNEL_CONFIG.exists()

    def install(self) -> None:
        log.info("Installing ICMPTunnel...")

        # Download the binary using the provided URL
        log.info(f"Downloading ICMPTunnel from: {ICMPTUNNEL_URL}")
        result = Shell.run(f"wget -O {ICMPTUNNEL_BIN} {ICMPTUNNEL_URL}", check=False, timeout=60)
        if not result.ok:
            log.error(f"Failed to download ICMPTunnel: {result.stderr}")
            raise Exception("ICMPTunnel download failed. Check network connectivity.")

        ICMPTUNNEL_BIN.chmod(0o755)

        # Create config directory
        ICMPTUNNEL_CONFIG.parent.mkdir(parents=True, exist_ok=True)

        data = state.ensure_defaults()
        domain = data.get("cert_domain", "0.0.0.0")
        key = data.get("icmp_tunnel_key", 12345678)

        # Server configuration (JSON format)
        config = {
            "type": "server",
            "listen_port_socks": "1010",
            "server": "",
            "timeout": 20,
            "dns": "8.8.8.8",
            "key": key,
            "api_port": "1080",
            "encrypt_data": True,
            "encrypt_data_key": "Ysh!io19HSwqi1ldm"
        }

        ICMPTUNNEL_CONFIG.write_text(json.dumps(config, indent=2))

        service = f"""
[Unit]
Description=ICMPTunnel ICMP Tunnel
After=network.target

[Service]
ExecStart={ICMPTUNNEL_BIN} -c {ICMPTUNNEL_CONFIG}
Restart=always
User=root

[Install]
WantedBy=multi-user.target
"""
        ICMPTUNNEL_SERVICE.write_text(service)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable icmptunnel", check=False)
        Shell.run("systemctl start icmptunnel", check=False, timeout=10)

        log.success("ICMPTunnel installed and running.")
        log.important("Client configuration:")
        log.important(f"  Server IP: {domain}")
        log.important(f"  Key: {key}")
        log.important("  SOCKS5 port: 1010")
        log.important("Use 'q-icmp' command to manage the tunnel (if install.sh was used).")

    def remove(self) -> None:
        Shell.run("systemctl stop icmptunnel", check=False)
        Shell.run("systemctl disable icmptunnel", check=False)
        ICMPTUNNEL_SERVICE.unlink(missing_ok=True)
        ICMPTUNNEL_CONFIG.unlink(missing_ok=True)
        ICMPTUNNEL_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("ICMPTunnel removed.")
