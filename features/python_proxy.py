import os
from pathlib import Path
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT
import time

PROXY_BIN = Path("/usr/local/bin/ws_ssh_proxy.py")
SERVICE_NAME = "ws-ssh-proxy.service"


class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages", "dropbear_service"]

    def is_installed(self) -> bool:
        return PROXY_BIN.exists() and Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
        dropbear_port = data.get("dropbear_port", 110)

        # Optimised proxy code – uvloop, fast connect, minimal retries
        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket
import logging

# Try to use uvloop for faster event loop
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logging.basicConfig(
    filename='/var/log/sshauto/proxy.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}

async def pipe(src, dst):
    try:
        while True:
            data = await src.read(16384)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception as e:
        logging.debug(f"pipe error: {{e}}")
    finally:
        dst.close()

async def handle_client(reader, writer):
    peername = writer.get_extra_info('peername')
    logging.info(f"New connection from {{peername}}")

    sock = writer.get_extra_info('socket')
    if sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

    # Read first line and headers
    try:
        first_line = await reader.readline()
    except Exception:
        writer.close()
        return
    if not first_line:
        writer.close()
        return

    parts = first_line.decode().strip().split()
    method = parts[0] if len(parts) > 0 else ''

    headers = {{}}
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break
        key, value = line.decode().strip().split(':', 1)
        headers[key.lower()] = value.strip()

    # Handle CONNECT: respond 200 OK and read the next request (Upgrade)
    if method.upper() == 'CONNECT':
        logging.info(f"CONNECT from {{peername}}")
        writer.write(b'HTTP/1.1 200 Connection Established\\r\\n\\r\\n')
        await writer.drain()
        try:
            first_line = await reader.readline()
            if not first_line:
                writer.close()
                return
            parts = first_line.decode().strip().split()
            method = parts[0] if len(parts) > 0 else ''
            headers = {{}}
            while True:
                line = await reader.readline()
                if not line or line == b'\\r\\n':
                    break
                key, value = line.decode().strip().split(':', 1)
                headers[key.lower()] = value.strip()
        except Exception as e:
            logging.error(f"Error reading upgrade: {{e}}")
            writer.close()
            return

    # Check for Upgrade: websocket
    upgrade = headers.get('upgrade', '').lower()
    if upgrade == 'websocket':
        logging.info(f"WebSocket upgrade from {{peername}}")
        writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
        writer.write(b'Upgrade: websocket\\r\\n')
        writer.write(b'Connection: Upgrade\\r\\n')
        writer.write(b'\\r\\n')
        await writer.drain()
    else:
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        return

    # Connect to Dropbear with a short timeout and one retry
    ssh_reader = ssh_writer = None
    for attempt in range(2):  # only 2 attempts total
        try:
            ssh_reader, ssh_writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', DROPBEAR_PORT),
                timeout=2.0
            )
            break
        except Exception as e:
            logging.warning(f"Dropbear connection attempt {{attempt+1}} failed: {{e}}")
            if attempt < 1:
                await asyncio.sleep(0.5)
            else:
                writer.close()
                return
    if not ssh_reader:
        writer.close()
        return

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

    logging.info(f"Tunnel established for {{peername}}")
    try:
        await asyncio.gather(
            pipe(reader, ssh_writer),
            pipe(ssh_reader, writer)
        )
    except Exception as e:
        logging.error(f"Tunnel error: {{e}}")
    finally:
        writer.close()
        ssh_writer.close()
        logging.info(f"Tunnel closed for {{peername}}")

async def main():
    server = await asyncio.start_server(
        handle_client, '127.0.0.1', PROXY_PORT,
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

    logging.info(f"Proxy listening on 127.0.0.1:{{PROXY_PORT}} (uvloop active)")
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        # Systemd service
        service_content = f"""[Unit]
Description=Forced-Upgrade TCP Proxy to SSH (Fast)
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
        service_path = Path(f"/etc/systemd/system/{SERVICE_NAME}")
        service_path.write_text(service_content)

        Shell.run("systemctl daemon-reload", timeout=10)
        Shell.run(f"systemctl enable {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl reset-failed {SERVICE_NAME}", check=False, timeout=10)

        # Start with retries
        success = False
        for attempt in range(3):
            result = Shell.run(f"systemctl start {SERVICE_NAME}", check=False, timeout=10)
            if result.ok:
                success = True
                break
            log.info(f"proxy start attempt {attempt+1}/3 failed, retrying...")
            time.sleep(1)

        if not success:
            Shell.run(f"systemctl status {SERVICE_NAME}", check=False)
            raise Exception("Proxy failed to start after multiple attempts.")

        if not self.is_installed():
            raise Exception("Proxy service installed but not active.")

        log.success("Python Proxy installed (fast – uvloop, short timeouts).")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False, timeout=10)
        Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", timeout=10)
        log.info("Python Proxy removed")
