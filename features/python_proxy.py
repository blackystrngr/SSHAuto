import os
from pathlib import Path
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT

# Script‑compatible paths
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

        # Exactly the proxy code from 4th.py
        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket

DROPBEAR_PORT = {dropbear_port}

async def force_upgrade_and_bridge(reader, writer):
    sock = writer.get_extra_info('socket')
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break

    writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
    writer.write(b'Upgrade: websocket\\r\\n')
    writer.write(b'Connection: Upgrade\\r\\n')
    writer.write(b'\\r\\n')
    await writer.drain()

    try:
        ssh_reader, ssh_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
    except Exception:
        writer.close()
        return

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
        force_upgrade_and_bridge, '127.0.0.1', {proxy_port},
        backlog=128,
        reuse_address=True,
    )
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
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        # Systemd service – identical to script
        service = f"""[Unit]
Description=Forced-Upgrade TCP Proxy to SSH (Low Latency)
After=network.target

[Service]
ExecStart=/usr/bin/python3 {PROXY_BIN}
Restart=always
User=root
StandardOutput=append:/var/log/sshauto/proxy.log
StandardError=append:/var/log/sshauto/proxy.log

[Install]
WantedBy=multi-user.target
"""
        Path(f"/etc/systemd/system/{SERVICE_NAME}").write_text(service)

        Shell.run("systemctl daemon-reload")
        Shell.run(f"systemctl enable {SERVICE_NAME}")
        Shell.run(f"systemctl restart {SERVICE_NAME}")

        if not self.is_installed():
            raise Exception("Proxy failed to start.")
        log.success("Python Proxy installed (script‑compatible).")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False)
        Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Python Proxy removed")
