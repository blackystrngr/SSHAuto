import os
from pathlib import Path
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return Path("/opt/sshauto/ws_proxy.py").exists() and \
               Shell.run("systemctl is-active sshauto-proxy", check=False).ok

    def install(self) -> None:
        log.info("Installing/Updating Python Proxy service...")
        
        data = state.ensure_defaults()
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
        dropbear_port = data.get("dropbear_port", 110)
        
        # Generate proxy script with DeepSeek optimizations
        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket

BACKEND_IP = "127.0.0.1"
BACKEND_PORT = {dropbear_port}
LISTEN_PORT = {proxy_port}

async def force_upgrade_and_bridge(reader, writer):
    # TCP_NODELAY on client socket (disable Nagle)
    sock = writer.get_extra_info('socket')
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # Read and discard HTTP headers (safe, no pushback)
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break

    # Send forced 101 Switching Protocols
    writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
    writer.write(b'Upgrade: websocket\\r\\n')
    writer.write(b'Connection: Upgrade\\r\\n')
    writer.write(b'\\r\\n')
    await writer.drain()

    # Connect to local Dropbear
    try:
        ssh_reader, ssh_writer = await asyncio.open_connection(BACKEND_IP, BACKEND_PORT)
    except Exception:
        writer.close()
        return

    # TCP_NODELAY on SSH socket
    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock is not None:
        ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    async def client_to_ssh():
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                ssh_writer.write(data)
                await ssh_writer.drain()
        except Exception:
            pass
        finally:
            ssh_writer.close()

    async def ssh_to_client():
        try:
            while True:
                data = await ssh_reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    await asyncio.gather(client_to_ssh(), ssh_to_client())

async def main():
    server = await asyncio.start_server(
        force_upgrade_and_bridge, '127.0.0.1', LISTEN_PORT,
        backlog=128,
        reuse_address=True,
    )
    # Apply low‑latency socket options to the listening socket
    for s in server.sockets:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        except:
            pass

    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
        proxy_path = Path("/opt/sshauto/ws_proxy.py")
        proxy_path.parent.mkdir(parents=True, exist_ok=True)
        proxy_path.write_text(proxy_code)
        proxy_path.chmod(0o755)

        # Create systemd service
        service_path = Path("/etc/systemd/system/sshauto-proxy.service")
        service_content = f"""[Unit]
Description=SSHAuto WebSocket Proxy (Low‑Latency)
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/sshauto/ws_proxy.py
Restart=always
User=root
StandardOutput=append:/var/log/sshauto/proxy.log
StandardError=append:/var/log/sshauto/proxy.log

[Install]
WantedBy=multi-user.target
"""
        service_path.write_text(service_content)

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable sshauto-proxy")
        Shell.run("systemctl restart sshauto-proxy")
        
        if not self.is_installed():
            raise Exception("Critical Failure: Python Proxy failed to start.")
        log.success("Python Proxy installed and verified.")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("systemctl disable sshauto-proxy", check=False)
        Path("/etc/systemd/system/sshauto-proxy.service").unlink(missing_ok=True)
        Path("/opt/sshauto/ws_proxy.py").unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Python Proxy removed")
