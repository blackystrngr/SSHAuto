"""
ICMP Tunneling – uses ptunnel to tunnel TCP over ICMP (ping) packets.
"""
from __future__ import annotations

from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PTUNNEL_BIN = Path("/usr/local/bin/ptunnel")
PTUNNEL_SERVICE = Path("/etc/systemd/system/ptunnel.service")


class IcmpTunnelFeature(BaseFeature):
    name = "icmp_tunnel"
    description = "ICMP tunneling (ptunnel) – uses ping packets"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return PTUNNEL_BIN.exists()

    def install(self) -> None:
        log.info("Installing ptunnel ICMP tunnel...")

        Shell.run("apt-get install -y build-essential libpcap-dev", check=True)

        url = "https://github.com/jamesbarlow/ptunnel/archive/refs/tags/v0.72.tar.gz"
        Shell.run(f"wget -O /tmp/ptunnel.tar.gz {url}", check=True)
        Shell.run("tar -xzf /tmp/ptunnel.tar.gz -C /tmp", check=True)
        Shell.run("cd /tmp/ptunnel-0.72 && make && cp ptunnel /usr/local/bin/", check=True)
        Shell.run("rm -rf /tmp/ptunnel*", check=False)

        service = f"""
[Unit]
Description=ptunnel ICMP tunnel
After=network.target

[Service]
ExecStart={PTUNNEL_BIN} -v 1 -c
Restart=always
User=root

[Install]
WantedBy=multi-user.target
"""
        PTUNNEL_SERVICE.write_text(service)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable ptunnel", check=False)
        Shell.run("systemctl start ptunnel", check=False, timeout=10)

        log.success("ICMP tunnel active.")
        log.important("Client: ptunnel -p <VPS_IP> -lp <local_port> -da <dest> -dp <port>")

    def remove(self) -> None:
        Shell.run("systemctl stop ptunnel", check=False)
        Shell.run("systemctl disable ptunnel", check=False)
        PTUNNEL_SERVICE.unlink(missing_ok=True)
        PTUNNEL_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("ICMP tunnel removed.")
