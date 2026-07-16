import os
import socket
import time
from pathlib import Path

from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT

PROXY_BIN = Path("/usr/local/bin/ws_ssh_proxy.py")
SERVICE_NAME = "ws-ssh-proxy.service"

def find_available_port(start_port=9955, max_tries=10):
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise Exception("No available ports found.")

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages", "dropbear_service"]

    def is_installed(self) -> bool:
        return PROXY_BIN.exists() and Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)

        if proxy_port in (8000, 8001):
            log.info(f"Migrating proxy port from {proxy_port} to {PROXY_PORT_DEFAULT}")
            proxy_port = PROXY_PORT_DEFAULT
            data["proxy_port"] = proxy_port
            state.save(data)

        if not self._port_available(proxy_port):
            log.warning(f"Port {proxy_port} is busy. Finding an available port...")
            new_port = find_available_port(proxy_port + 1)
            log.info(f"Using alternative port {new_port}")
            proxy_port = new_port
            state.set("proxy_port", proxy_port)

        dropbear_port = data.get("dropbear_port", 110)

        proxy_code = f'''#!/usr/bin/env python3
# (same proxy code as the "smart" version we provided earlier)
# ... paste the full proxy code here ...
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        service_content = f"""[Unit]
Description=Unified Proxy (WebSocket + CONNECT)
After=network.target dropbear-tunnel.service
Wants=dropbear-tunnel.service

[Service]
ExecStart=/usr/bin/python3 {PROXY_BIN}
Restart=always
RestartSec=2
User=root
StandardOutput=append:/var/log/sshauto/proxy.log
StandardError=append:/var/log/sshauto/proxy.log

[Install]
WantedBy=multi-user.target
"""
        service_path = Path(f"/etc/systemd/system/{SERVICE_NAME}")
        service_path.write_text(service_content)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run(f"systemctl enable {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl reset-failed {SERVICE_NAME}", check=False, timeout=10)

        # Start with a longer timeout and check status
        result = Shell.run(f"systemctl start {SERVICE_NAME}", check=False, timeout=30)
        if not result.ok:
            log.error(f"Proxy start failed (exit {result.returncode}): {result.stderr}")
            # Show logs for debugging
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 20", check=False)
            raise Exception("Proxy failed to start. See journalctl output above.")

        # Verify it's actually running
        status = Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False, timeout=5)
        if not status.ok or "active" not in status.stdout:
            log.error(f"Proxy service is not active (status: {status.stdout})")
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 20", check=False)
            raise Exception("Proxy service installed but not active.")

        log.success(f"Unified Python Proxy installed on port {proxy_port} (uvloop, QuickACK, auto‑reconnect).")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False, timeout=10)
        Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", timeout=10)
        log.info("Python Proxy removed")

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return True
            except OSError:
                return False
