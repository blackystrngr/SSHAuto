import os
from pathlib import Path
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT

PROXY_BIN = Path("/usr/local/bin/ws_ssh_proxy.py")
SERVICE_NAME = "ws-ssh-proxy.service"

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return PROXY_BIN.exists() and Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
        dropbear_port = data.get("dropbear_port", 110)

        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket
import time

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}

async def force_upgrade_and_bridge(reader, writer):
    sock = writer.get_extra_info('socket')
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

    # Read HTTP headers
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break

    # Send 101
    writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
    writer.write(b'Upgrade: websocket\\r\\n')
    writer.write(b'Connection: Upgrade\\r\\n')
    writer.write(b'\\r\\n')
    await writer.drain()

    # Connect to Dropbear with retries
    ssh_reader = ssh_writer = None
    for attempt in range(3):
        try:
            ssh_reader, ssh_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
            break
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.5)
            else:
                writer.close()
                return
    if not ssh_reader:
        writer.close()
        return

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock is not None:
        ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

    async def pipe(src, dst):
        try:
            while True:
                data = await src.read(16384)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            dst.close()

    await asyncio.gather(pipe(reader, ssh_writer), pipe(ssh_reader, writer))

async def main():
    server = await asyncio.start_server(
        force_upgrade_and_bridge, '127.0.0.1', PROXY_PORT,
        backlog=256,
        reuse_address=True,
    )
    for s in server.sockets:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        except:
            pass

    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        service = f"""[Unit]
Description=Forced-Upgrade TCP Proxy to SSH (Low Latency)
After=network.target dropbear-tunnel.service
Wants=dropbear-tunnel.service

[Service]
ExecStart=/usr/bin/python3 {PROXY_BIN}
Restart=always
RestartSec=3
User=root
StandardOutput=append:/var/log/sshauto/proxy.log
StandardError=append:/var/log/sshauto/proxy.log

[Install]
WantedBy=multi-user.target
"""
        Path(f"/etc/systemd/system/{SERVICE_NAME}").write_text(service)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run(f"systemctl enable {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl restart {SERVICE_NAME}", check=False, timeout=10)

        if not self.is_installed():
            raise Exception("Proxy failed to start.")
        log.success("Python Proxy installed (with retry logic).")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False, timeout=10)
        Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", timeout=10)
        log.info("Python Proxy removed")
