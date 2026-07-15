"""
ICMP Tunneling using pingtunnel (Go‑based) – compiled from source.
"""
from __future__ import annotations

from pathlib import Path
from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PINGTUNNEL_BIN = Path("/usr/local/bin/pingtunnel")
PINGTUNNEL_SERVICE = Path("/etc/systemd/system/pingtunnel.service")


class IcmpTunnelFeature(BaseFeature):
    name = "icmp_tunnel"
    description = "ICMP tunneling using pingtunnel (Go‑based)"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return PINGTUNNEL_BIN.exists() and PINGTUNNEL_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing pingtunnel ICMP tunnel from source...")

        data = state.ensure_defaults()
        key = data.get("icmp_tunnel_key", 123456)

        # 1. Disable kernel ICMP replies
        log.info("Disabling kernel ICMP echo replies...")
        Shell.run("sysctl -w net.ipv4.icmp_echo_ignore_all=1", check=False)
        Shell.run('echo "net.ipv4.icmp_echo_ignore_all=1" | tee -a /etc/sysctl.conf', check=False)

        # 2. Install Go and build tools
        Shell.run("apt-get install -y golang-go git make", check=True)

        # 3. Clone and build pingtunnel from source
        log.info("Cloning and building pingtunnel from source...")
        Shell.run("git clone https://github.com/esrrhs/pingtunnel.git /tmp/pingtunnel", check=True)
        Shell.run("cd /tmp/pingtunnel && go build -o pingtunnel", check=True)
        Shell.run("cp /tmp/pingtunnel/pingtunnel /usr/local/bin/", check=True)
        Shell.run("chmod +x /usr/local/bin/pingtunnel", check=True)
        Shell.run("rm -rf /tmp/pingtunnel", check=False)

        # 4. Create systemd service
        service_content = f"""
[Unit]
Description=Pingtunnel ICMP Server
After=network.target

[Service]
Type=simple
User=root
ExecStart={PINGTUNNEL_BIN} -type server -key {key}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        PINGTUNNEL_SERVICE.write_text(service_content)
        log.info(f"Systemd service created with key: {key}")

        # 5. Enable and start the service
        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable pingtunnel", check=False)
        Shell.run("systemctl start pingtunnel", check=False, timeout=10)

        status = Shell.run("systemctl is-active pingtunnel", check=False, timeout=5)
        if status.ok and "active" in status.stdout:
            log.success("Pingtunnel ICMP tunnel installed and running.")
        else:
            log.warning("Pingtunnel service may not be active. Check with 'systemctl status pingtunnel'")

        log.important("Client configuration:")
        log.important(f"  Server IP: your_vps_ip (ICMP protocol)")
        log.important(f"  Key: {key} (numeric)")
        log.important("  Use a client that supports pingtunnel (e.g., HTTP Custom, NetMod)")

    def remove(self) -> None:
        Shell.run("systemctl stop pingtunnel", check=False)
        Shell.run("systemctl disable pingtunnel", check=False)
        PINGTUNNEL_SERVICE.unlink(missing_ok=True)
        PINGTUNNEL_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        Shell.run("sysctl -w net.ipv4.icmp_echo_ignore_all=0", check=False)
        log.info("ICMP tunnel removed.")
