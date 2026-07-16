"""
ICMP Tunneling – pingtunnel (optional).
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
    description = "ICMP tunneling (pingtunnel) – optional"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return PINGTUNNEL_BIN.exists() and PINGTUNNEL_SERVICE.exists()

    def install(self) -> None:
        log.info("Installing pingtunnel...")

        data = state.ensure_defaults()
        key = data.get("icmp_tunnel_key", 123456)
        server_ip = data.get("server_ip", "your_server_ip")

        # 1. Disable kernel ICMP replies
        log.info("Disabling kernel ICMP echo replies...")
        Shell.run("sysctl -w net.ipv4.icmp_echo_ignore_all=1", check=False)
        Shell.run('echo "net.ipv4.icmp_echo_ignore_all=1" | tee -a /etc/sysctl.conf', check=False)

        # 2. Detect architecture and download correct zip
        arch = Shell.run("uname -m", check=True).stdout.strip()
        if arch == "x86_64":
            file_arch = "linux_amd64"
        elif arch == "aarch64":
            file_arch = "linux_arm64"
        else:
            raise Exception(f"Unsupported architecture: {arch}")

        log.info(f"Architecture: {arch} -> {file_arch}")
        url = f"https://github.com/esrrhs/pingtunnel/releases/download/2.8/pingtunnel_{file_arch}.zip"
        log.info(f"Downloading from: {url}")

        Shell.run("mkdir -p /tmp/pingtunnel_setup", check=True)
        Shell.run(f"wget -O /tmp/pingtunnel_setup/pingtunnel.zip {url}", check=True)
        Shell.run("cd /tmp/pingtunnel_setup && unzip -o pingtunnel.zip", check=True)
        Shell.run("mv /tmp/pingtunnel_setup/pingtunnel /usr/local/bin/", check=True)
        Shell.run("chmod +x /usr/local/bin/pingtunnel", check=True)
        Shell.run("rm -rf /tmp/pingtunnel_setup", check=False)

        # 3. Create systemd service
        service_content = f"""
[Unit]
Description=Pingtunnel ICMP Server
After=network.target

[Service]
Type=simple
User=root
ExecStart={PINGTUNNEL_BIN} -type server -key {key}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        PINGTUNNEL_SERVICE.write_text(service_content)
        log.info(f"Service created with key: {key}")

        # 4. Enable and start
        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run("systemctl enable pingtunnel", check=False)
        Shell.run("systemctl start pingtunnel", check=False, timeout=10)

        status = Shell.run("systemctl is-active pingtunnel", check=False, timeout=5)
        if status.ok and "active" in status.stdout:
            log.success("Pingtunnel installed and running.")
        else:
            log.warning("Pingtunnel may not be active.")

        log.important("Client config:")
        log.important(f"  Server IP: {server_ip} (ICMP)")
        log.important(f"  Key: {key} (numeric)")

    def remove(self) -> None:
        Shell.run("systemctl stop pingtunnel", check=False)
        Shell.run("systemctl disable pingtunnel", check=False)
        PINGTUNNEL_SERVICE.unlink(missing_ok=True)
        PINGTUNNEL_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        Shell.run("sysctl -w net.ipv4.icmp_echo_ignore_all=0", check=False)
        log.info("Pingtunnel removed.")
