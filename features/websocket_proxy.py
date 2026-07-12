"""
WebSocket-to-TCP proxy for SSH relay.

This bridge fakes the HTTP/WebSocket handshake and then forwards raw bytes
between the client and dropbear. It runs as a systemd service.
"""
from __future__ import annotations

from core.config import APP_ROOT, DROPBEAR_PORT_DEFAULT, LOG_DIR, SYSTEMD_DIR, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PROXY_PORT_DEFAULT = 109
PROXY_SCRIPT_PATH = APP_ROOT / "proxy" / "websocket_proxy.py"
PROXY_SERVICE_NAME = "sshauto-proxy.service"
PROXY_SERVICE_PATH = SYSTEMD_DIR / PROXY_SERVICE_NAME


class WebSocketProxyFeature(BaseFeature):
    name = "websocket_proxy"
    description = "WebSocket-to-TCP bridge (required for nginx ↔ dropbear)"
    depends_on = ["packages", "dropbear_service"]

    def is_installed(self) -> bool:
        return PROXY_SERVICE_PATH.exists() and self._service_enabled()

    def install(self) -> None:
        data = state.ensure_defaults()
        proxy_port = data.get("websocket_proxy_port", PROXY_PORT_DEFAULT)
        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        # Write the proxy script
        self._write_script()

        # Write the systemd service file
        service_content = f"""# Managed by sshauto - do not edit
[Unit]
Description=SSHauto WebSocket-to-TCP proxy
After=network.target dropbear.service
Wants=dropbear.service

[Service]
Type=simple
WorkingDirectory={APP_ROOT}
ExecStart=/usr/bin/python3 {PROXY_SCRIPT_PATH} --listen 127.0.0.1:{proxy_port} --backend 127.0.0.1:{dropbear_port}
Restart=on-failure
RestartSec=5
StandardOutput=append:{LOG_DIR}/proxy.log
StandardError=append:{LOG_DIR}/proxy.log

[Install]
WantedBy=multi-user.target
"""
        PROXY_SERVICE_PATH.write_text(service_content)
        Shell.run("systemctl daemon-reload")
        Shell.run(f"systemctl enable {PROXY_SERVICE_NAME}")
        Shell.run(f"systemctl restart {PROXY_SERVICE_NAME}")

        state.set("websocket_proxy_port", proxy_port)
        log.success(f"WebSocket proxy listening on 127.0.0.1:{proxy_port}, backend dropbear:{dropbear_port}")

        # Now regenerate nginx config to point to the proxy
        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()

    def remove(self) -> None:
        Shell.run(f"systemctl disable --now {PROXY_SERVICE_NAME}", check=False)
        PROXY_SERVICE_PATH.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.success("WebSocket proxy removed")

    def _write_script(self) -> None:
        """Write the proxy script to disk."""
        PROXY_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        script_content = '''#!/usr/bin/env python3
"""
Simple WebSocket-to-TCP proxy.
Listens on a port, accepts connections, fakes a WebSocket handshake,
then relays raw bytes to a backend TCP socket.
"""
import argparse
import socket
import select
import sys
import threading
import time

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BUFFER_SIZE = 65536
IDLE_TIMEOUT = 3600  # seconds


class ConnectionHandler(threading.Thread):
    def __init__(self, client_sock, backend_addr, idle_timeout=IDLE_TIMEOUT):
        super().__init__(daemon=True)
        self.client = client_sock
        self.backend_addr = backend_addr
        self.idle_timeout = idle_timeout

    def run(self):
        try:
            backend = socket.create_connection(self.backend_addr, timeout=5)
        except Exception as e:
            self._send_error(b"HTTP/1.1 502 Bad Gateway\\r\\n\\r\\n")
            self.client.close()
            return

        # Read initial data from client (HTTP headers or SSH banner)
        try:
            data = self.client.recv(BUFFER_SIZE)
            if not data:
                self.client.close()
                backend.close()
                return
        except:
            self.client.close()
            backend.close()
            return

        # Check if it looks like an HTTP CONNECT or upgrade request
        # We'll fake a successful handshake accordingly.
        http_lines = data.split(b"\\r\\n")
        is_connect = any(line.startswith(b"CONNECT ") for line in http_lines)
        is_upgrade = any(b"Upgrade: websocket" in line for line in http_lines)

        response = b""
        if is_connect:
            # HTTP CONNECT proxy method
            response = b"HTTP/1.1 200 Connection Established\\r\\n\\r\\n"
        elif is_upgrade or b"Upgrade" in data:
            # Fake WebSocket upgrade (101)
            response = (
                b"HTTP/1.1 101 Switching Protocols\\r\\n"
                b"Upgrade: websocket\\r\\n"
                b"Connection: Upgrade\\r\\n"
                b"Content-Length: 0\\r\\n"
                b"Sec-WebSocket-Accept: fake\\r\\n"
                b"\\r\\n"
            )
        else:
            # Raw data – assume it's SSH or other binary; forward immediately
            # without a fake response.
            pass

        # Send handshake response if any
        if response:
            self.client.sendall(response)

        # Now relay bytes in both directions
        self._relay(self.client, backend, data if not response else None)

    def _relay(self, client, backend, initial_data=None):
        # Send initial data if we didn't consume it
        if initial_data:
            try:
                backend.sendall(initial_data)
            except:
                pass

        sockets = [client, backend]
        last_activity = time.time()
        while True:
            rlist, _, _ = select.select(sockets, [], [], 1)
            now = time.time()
            if now - last_activity > self.idle_timeout:
                break
            for sock in rlist:
                try:
                    data = sock.recv(BUFFER_SIZE)
                    if not data:
                        return
                    if sock is client:
                        backend.sendall(data)
                    else:
                        client.sendall(data)
                    last_activity = now
                except:
                    return

    def _send_error(self, msg):
        try:
            self.client.sendall(msg)
        except:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:109", help="listen address:port")
    parser.add_argument("--backend", default="127.0.0.1:143", help="backend address:port")
    args = parser.parse_args()

    host, port_str = args.listen.split(":")
    port = int(port_str)
    backend_host, backend_port_str = args.backend.split(":")
    backend_port = int(backend_port_str)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(128)
    print(f"Proxy listening on {host}:{port}, forwarding to {backend_host}:{backend_port}")

    while True:
        client, _ = server.accept()
        handler = ConnectionHandler(client, (backend_host, backend_port))
        handler.start()

if __name__ == "__main__":
    main()
'''
        PROXY_SCRIPT_PATH.write_text(script_content)
        PROXY_SCRIPT_PATH.chmod(0o755)

    def _service_enabled(self) -> bool:
        result = Shell.run(f"systemctl is-enabled {PROXY_SERVICE_NAME}", check=False)
        return result.ok and "enabled" in result.stdout
