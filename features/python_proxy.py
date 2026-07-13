"""
Python asyncio websocket-to-TCP proxy.
Listens on 127.0.0.1:<proxy_port>, handles WebSocket upgrade,
then forwards raw bytes to dropbear on 127.0.0.1:<dropbear_port>.
"""
from __future__ import annotations

from pathlib import Path

from core.config import APP_ROOT, DROPBEAR_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PROXY_PORT_DEFAULT = 8000
PROXY_SCRIPT = APP_ROOT / "proxy" / "ws_proxy.py"
PROXY_SERVICE = "/etc/systemd/system/sshauto-proxy.service"


class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    description = "Install the asyncio WebSocket-to-TCP relay"
    depends_on = ["packages"]   # only needs python3

    def is_installed(self) -> bool:
        return PROXY_SCRIPT.exists() and Path(PROXY_SERVICE).exists()

    def install(self) -> None:
        data = state.ensure_defaults()
        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)

        log.info(f"Writing WebSocket proxy to {PROXY_SCRIPT}")
        PROXY_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        PROXY_SCRIPT.write_text(self._proxy_code(dropbear_port, proxy_port))
        PROXY_SCRIPT.chmod(0o755)

        log.info(f"Creating systemd service: {PROXY_SERVICE}")
        service_content = f"""[Unit]
Description=sshauto WebSocket proxy
After=network-online.target dropbear.service
Wants=dropbear.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {PROXY_SCRIPT}
Restart=always
RestartSec=5
User=root
StandardOutput=append:/var/log/sshauto/proxy.log
StandardError=append:/var/log/sshauto/proxy.log

[Install]
WantedBy=multi-user.target
"""
        Path(PROXY_SERVICE).write_text(service_content)

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable sshauto-proxy")
        Shell.run("systemctl restart sshauto-proxy")
        log.success(f"Proxy listening on 127.0.0.1:{proxy_port}, forwarding to dropbear:{dropbear_port}")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("systemctl disable sshauto-proxy", check=False)
        Path(PROXY_SERVICE).unlink(missing_ok=True)
        PROXY_SCRIPT.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Python proxy removed")

    # ---------- helper to generate the proxy script ----------
    def _proxy_code(self, dropbear_port: int, proxy_port: int) -> str:
        return f'''#!/usr/bin/env python3
"""
WebSocket-to-TCP proxy for sshauto.
Listens on 127.0.0.1:{proxy_port}, handles websocket handshake,
then forwards raw TCP to 127.0.0.1:{dropbear_port}.
"""
import asyncio
import sys

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}

def parse_headers(data):
    lines = data.decode('utf-8', errors='ignore').split('\\r\\n')
    headers = {{}}
    for line in lines[1:]:
        if ': ' in line:
            key, val = line.split(': ', 1)
            headers[key.lower()] = val
    return headers

async def handle_client(reader, writer):
    try:
        # Read the HTTP request
        request = await reader.read(4096)
        if not request:
            return

        # Check for Upgrade: websocket
        headers = parse_headers(request)
        if headers.get('upgrade', '').lower() != 'websocket':
            # Not a websocket request – close or return 400
            writer.write(b"HTTP/1.1 400 Bad Request\\r\\n\\r\\n")
            await writer.drain()
            return

        # Accept the WebSocket upgrade
        key = headers.get('sec-websocket-key', '')
        accept = base64.b64encode(sha1(key.encode() + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest()).decode()
        response = (
            "HTTP/1.1 101 Switching Protocols\\r\\n"
            "Upgrade: websocket\\r\\n"
            "Connection: Upgrade\\r\\n"
            f"Sec-WebSocket-Accept: {{accept}}\\r\\n\\r\\n"
        )
        writer.write(response.encode())
        await writer.drain()

        # Now connect to dropbear
        try:
            db_reader, db_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
        except Exception:
            writer.close()
            return

        # Bidirectional raw forwarding
        async def forward(src, dst):
            try:
                while True:
                    data = await src.read(8192)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                dst.close()

        await asyncio.gather(forward(reader, db_writer), forward(db_reader, writer))

    except Exception:
        pass
    finally:
        writer.close()

async def main():
    server = await asyncio.start_server(handle_client, '127.0.0.1', PROXY_PORT)
    print(f"[+] WebSocket proxy running on 127.0.0.1:{{PROXY_PORT}} -> dropbear:{{DROPBEAR_PORT}}")
    await server.serve_forever()

if __name__ == '__main__':
    import base64, hashlib
    from hashlib import sha1
    asyncio.run(main())
'''
