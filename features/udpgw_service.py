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
        unit = Path("/etc/systemd/system/badvpn-udpgw.service")
        # An older unit bound 0.0.0.0:7300 (unauthenticated, public); treat it as not installed so a
        # normal re-run rewrites it to loopback.
        return Path("/usr/local/bin/badvpn-udpgw").exists() and unit.exists() and \
               "--listen-addr 127.0.0.1:7300" in unit.read_text()

    def install(self) -> None:
        log.info("Ensuring compilation tools are available...")
        Shell.run("apt-get update -y && apt-get install -y cmake make git gcc g++", check=True)

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

        # Loopback only: udpgw has no authentication. Clients reach it through their SSH tunnel
        # (the SSH client connects to 127.0.0.1:7300 on the server side).
        service_content = """[Unit]
Description=BadVPN UDP Gateway Daemon
After=network.target

[Service]
ExecStart=/usr/local/bin/badvpn-udpgw --loglevel warning --listen-addr 127.0.0.1:7300 --max-clients 1000 --max-connections-for-client 10
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""
        Path("/etc/systemd/system/badvpn-udpgw.service").write_text(service_content)

        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        Shell.run("systemctl enable badvpn-udpgw", check=True)
        Shell.run("systemctl restart badvpn-udpgw", check=True)

        log.success("UDP Gateway active on 127.0.0.1:7300 (reachable only through an authenticated SSH tunnel).")
        log.important("Do NOT open port 7300 in any firewall. In your client set the UDPGW address to 127.0.0.1:7300.")

    def remove(self) -> None:
        # udpgw (binary, unit, service) is kept on uninstall.
        log.info("UDP Gateway kept.")
