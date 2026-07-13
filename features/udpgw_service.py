from __future__ import annotations

import shutil
from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

class UdpgwServiceFeature(BaseFeature):
    name = "udpgw_service"
    description = "Compile and deploy badvpn-udpgw for UDP tunnel forwarding (Gaming/VoIP)"
    depends_on = ["packages"]
    idempotent = True

    def is_installed(self) -> bool:
        binary_exists = Path("/usr/local/bin/badvpn-udpgw").exists()
        service_exists = Path("/etc/systemd/system/badvpn-udpgw.service").exists()
        return binary_exists and service_exists

    def install(self) -> None:
        log.info("Ensuring compilation tools are available...")
        Shell.run("apt-get update -y && apt-get install -y cmake make git gcc g++", check=True)

        # STEP 1: Compile badvpn-udpgw
        if not Path("/usr/local/bin/badvpn-udpgw").exists():
            log.info("Cloning and compiling badvpn-udpgw...")
            src_dir = Path("/tmp/badvpn_src")
            if src_dir.exists():
                shutil.rmtree(src_dir)
            Shell.run("git clone --depth 1 https://github.com/ambrop72/badvpn.git /tmp/badvpn_src", check=True)
            build_dir = src_dir / "badvpn-build"
            build_dir.mkdir(parents=True, exist_ok=True)
            Shell.run(
                "cd /tmp/badvpn_src/badvpn-build && cmake .. -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 && make",
                check=True
            )
            Shell.run("cp /tmp/badvpn_src/badvpn-build/udpgw/badvpn-udpgw /usr/local/bin/badvpn-udpgw", check=True)
            Shell.run("chmod +x /usr/local/bin/badvpn-udpgw", check=True)
            shutil.rmtree(src_dir)
            log.success("badvpn-udpgw binary compiled successfully.")
        else:
            log.info("badvpn-udpgw binary already available, skipping compilation.")

        # STEP 2: Deploy systemd service – bind to 0.0.0.0:7300 (public)
        log.info("Configuring badvpn-udpgw systemd service daemon...")
        service_content = """[Unit]
Description=BadVPN UDP Gateway Daemon for SSH Tunneling
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/badvpn-udpgw --loglevel warning --listen-addr 0.0.0.0:7300 --max-clients 1000 --max-connections-for-client 10
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        Path("/etc/systemd/system/badvpn-udpgw.service").write_text(service_content)

        Shell.run("systemctl daemon-reload", check=True)
        Shell.run("systemctl enable badvpn-udpgw", check=True)
        Shell.run("systemctl restart badvpn-udpgw", check=True)

        log.success("UDP Gateway service is fully deployed and active on port 7300 (public).")

    def remove(self) -> None:
        log.info("Teardown initiated for UDP Gateway component...")
        Shell.run("systemctl stop badvpn-udpgw", check=False)
        Shell.run("systemctl disable badvpn-udpgw", check=False)

        service_file = Path("/etc/systemd/system/badvpn-udpgw.service")
        if service_file.exists():
            service_file.unlink()

        binary_file = Path("/usr/local/bin/badvpn-udpgw")
        if binary_file.exists():
            binary_file.unlink()

        Shell.run("systemctl daemon-reload", check=False)
        log.success("UDP Gateway service stack fully uninstalled.")
