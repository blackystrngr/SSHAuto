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
import asyncio
import socket
import logging
import time
import sys

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    uvloop_used = True
except ImportError:
    uvloop_used = False

logging.basicConfig(
    filename='/var/log/sshauto/proxy.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logging.info(f"Proxy starting, uvloop: {{uvloop_used}}")

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}

def set_socket_quickack(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, 12, 1)
    except Exception:
        pass

async def pipe(src, dst, direction=""):
    try:
        while True:
            data = await src.read(16384)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception as e:
        logging.debug(f"pipe {{direction}} error: {{e}}")
    finally:
        dst.close()

async def handle_connect(reader, writer, target_host, target_port):
    """Handle HTTP CONNECT: establish tunnel to target_host:target_port."""
    logging.info(f"CONNECT to {{target_host}}:{{target_port}}")
    try:
        # Connect to target
        ssh_reader, ssh_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=5.0
        )
    except Exception as e:
        logging.error(f"CONNECT to {{target_host}}:{{target_port}} failed: {{e}}")
        writer.write(b'HTTP/1.1 502 Bad Gateway\\r\\n\\r\\n')
        await writer.drain()
        writer.close()
        return

    # Send 200 Connection Established
    writer.write(b'HTTP/1.1 200 Connection Established\\r\\n\\r\\n')
    await writer.drain()

    # Tunnel data
    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        set_socket_quickack(ssh_sock)

    try:
        await asyncio.gather(
            pipe(reader, ssh_writer, "client->ssh"),
            pipe(ssh_reader, writer, "ssh->client")
        )
    except Exception as e:
        logging.error(f"CONNECT tunnel error: {{e}}")
    finally:
        writer.close()
        ssh_writer.close()

async def handle_websocket(reader, writer):
    """Handle WebSocket upgrade: connect to Dropbear and tunnel."""
    writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
    writer.write(b'Upgrade: websocket\\r\\n')
    writer.write(b'Connection: Upgrade\\r\\n')
    writer.write(b'\\r\\n')
    await writer.drain()

    try:
        ssh_reader, ssh_writer = await asyncio.wait_for(
            asyncio.open_connection('127.0.0.1', DROPBEAR_PORT),
            timeout=2.0
        )
    except Exception as e:
        logging.error(f"Dropbear connection failed: {{e}}")
        writer.close()
        return

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        ssh_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
        ssh_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        set_socket_quickack(ssh_sock)

    try:
        await asyncio.gather(
            pipe(reader, ssh_writer, "client->ssh"),
            pipe(ssh_reader, writer, "ssh->client")
        )
    except Exception as e:
        logging.error(f"WebSocket tunnel error: {{e}}")
    finally:
        writer.close()
        ssh_writer.close()

async def handle_client(reader, writer):
    start_time = time.time()
    peername = writer.get_extra_info('peername')
    logging.info(f"New connection from {{peername}}")

    sock = writer.get_extra_info('socket')
    if sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        set_socket_quickack(sock)

    # Read request line
    try:
        first_line = await reader.readline()
    except Exception:
        writer.close()
        return
    if not first_line:
        writer.close()
        return
    elapsed = round((time.time() - start_time) * 1000)
    logging.info(f"Read first_line in {{elapsed}}ms")

    parts = first_line.decode().strip().split()
    if len(parts) < 3:
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        return
    method, raw_target, version = parts[0], parts[1], parts[2]

    # Read headers
    headers = {{}}
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break
        key, value = line.decode().strip().split(':', 1)
        headers[key.lower()] = value.strip()

    elapsed = round((time.time() - start_time) * 1000)
    logging.info(f"Headers read in {{elapsed}}ms")

    # --- 1. HTTP CONNECT ---
    if method.upper() == 'CONNECT':
        # Parse target: host:port
        if ':' not in raw_target:
            writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
            writer.close()
            return
        host, port_str = raw_target.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
            writer.close()
            return
        # Only allow localhost Dropbear? We'll allow any, but add a note.
        logging.info(f"CONNECT request to {{host}}:{{port}} from {{peername}}")
        await handle_connect(reader, writer, host, port)
        return

    # --- 2. WebSocket Upgrade ---
    upgrade = headers.get('upgrade', '').lower()
    if upgrade == 'websocket':
        logging.info(f"WebSocket upgrade from {{peername}}")
        await handle_websocket(reader, writer)
        return

    # --- 3. Other methods ---
    writer.write(b'HTTP/1.1 405 Method Not Allowed\\r\\n\\r\\n')
    writer.close()
    logging.info(f"Unsupported method {{method}} from {{peername}}")

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
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
            set_socket_quickack(s)
        except:
            pass

    logging.info(f"Proxy listening on 127.0.0.1:{{PROXY_PORT}} (uvloop: {{uvloop_used}})")
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        # Systemd service
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

        result = Shell.run(f"systemctl start {SERVICE_NAME}", check=False, timeout=10)
        if not result.ok:
            log.error(f"Proxy start failed: {result.stderr}")
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 10", check=False)
            raise Exception("Proxy failed to start.")

        if not self.is_installed():
            raise Exception("Proxy service installed but not active.")

        log.success(f"Unified Python Proxy installed on port {proxy_port} (CONNECT + WebSocket).")

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
