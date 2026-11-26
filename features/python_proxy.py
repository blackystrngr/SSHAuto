import socket
import time
from pathlib import Path

from core.config import DROPBEAR_PORT_DEFAULT, LOG_DIR, PROXY_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

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
            proxy_port = PROXY_PORT_DEFAULT
            data["proxy_port"] = proxy_port
            state.save(data)

        if not self._port_available(proxy_port):
            new_port = find_available_port(proxy_port + 1)
            proxy_port = new_port
            state.set("proxy_port", proxy_port)

        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._ensure_uvloop()

        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket
import logging
import time
import sys
from pathlib import Path
import urllib.parse

import uvloop  # required: installer guarantees it

LOG_DIR = Path("/var/log/sshauto")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "proxy.log"),
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(message)s'
)
logging.info("Proxy starting (uvloop)")

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}
READ_CHUNK = 65536  # 64KB
STREAM_LIMIT = 262144  # 256KB stream buffer
BIND_HOST = "127.0.0.1"  # nginx is the only public listener

def tune_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        sock.setsockopt(socket.IPPROTO_TCP, 12, 1)  # TCP_QUICKACK
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 30000)
        except Exception:
            pass
    except Exception:
        pass

async def pipe(src, dst, direction=""):
    try:
        while True:
            data = await src.read(READ_CHUNK)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception as e:
        logging.debug(f"pipe {{direction}} error: {{e}}")
    finally:
        dst.close()

async def handle_http_forward(reader, writer, method, raw_target, headers, first_line):
    """Forward plain HTTP request to the target from Host header."""
    host_header = headers.get('host')
    if not host_header:
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        return

    # Parse host:port (default port 80 if none)
    if ':' in host_header:
        host, port_str = host_header.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 80
    else:
        host = host_header
        port = 80

    logging.info(f"Forwarding HTTP request to {{host}}:{{port}} (method: {{method}})")

    try:
        target_reader, target_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=STREAM_LIMIT),
            timeout=10.0
        )
    except Exception as e:
        logging.error(f"Forward connection failed: {{e}}")
        writer.write(b'HTTP/1.1 502 Bad Gateway\\r\\n\\r\\n')
        await writer.drain()
        writer.close()
        return

    # Send the original request (first line + headers + body)
    target_writer.write(first_line)
    for key, value in headers.items():
        # Rebuild headers (skip Host and Connection? We'll keep them)
        target_writer.write(f"{{key}}: {{value}}\\r\\n".encode())
    target_writer.write(b'\\r\\n')

    # If there is a request body, read and forward it (we need to read it first)
    # For simplicity, we assume no body or we pipe the rest of the client stream
    # The client may have already sent the body, so we'll pipe remaining data.
    async def client_to_target():
        try:
            while True:
                data = await reader.read(READ_CHUNK)
                if not data:
                    break
                target_writer.write(data)
                await target_writer.drain()
        except Exception:
            pass

    async def target_to_client():
        try:
            while True:
                data = await target_reader.read(READ_CHUNK)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass

    try:
        await asyncio.gather(client_to_target(), target_to_client())
    except Exception as e:
        logging.error(f"HTTP forward error: {{e}}")
    finally:
        writer.close()
        target_writer.close()

async def handle_client(reader, writer):
    peername = writer.get_extra_info('peername')
    logging.info(f"New connection from {{peername}}")

    sock = writer.get_extra_info('socket')
    if sock:
        tune_socket(sock)

    try:
        first_line = await reader.readline()
    except Exception:
        writer.close()
        return
    if not first_line:
        writer.close()
        return

    # --- Reject raw SSH ---
    if first_line.startswith(b'SSH-'):
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        logging.info(f"Rejected raw SSH from {{peername}}")
        return

    # --- Parse HTTP request ---
    try:
        parts = first_line.decode().strip().split()
        if len(parts) < 3:
            writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
            writer.close()
            return
        method, raw_target, version = parts[0], parts[1], parts[2]
    except Exception:
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        return

    headers = {{}}
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break
        key, sep, value = line.decode(errors='replace').partition(':')
        if sep:
            headers[key.strip().lower()] = value.strip()

    # --- WebSocket upgrade ---
    upgrade = headers.get('upgrade', '').lower()
    if upgrade == 'websocket':
        logging.info(f"WebSocket upgrade from {{peername}} (method: {{method}})")
        await handle_websocket(reader, writer)
        return

    # --- CONNECT ---
    if method.upper() == 'CONNECT':
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
        logging.info(f"CONNECT to {{host}}:{{port}} from {{peername}}")
        await handle_connect(reader, writer, host, port)
        return

    # --- Plain HTTP forward (GET, POST, etc.) ---
    await handle_http_forward(reader, writer, method, raw_target, headers, first_line)

async def handle_connect(reader, writer, host, port):
    try:
        ssh_reader, ssh_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=STREAM_LIMIT),
            timeout=10.0
        )
    except Exception as e:
        logging.error(f"CONNECT failed: {{e}}")
        writer.write(b'HTTP/1.1 502 Bad Gateway\\r\\n\\r\\n')
        await writer.drain()
        writer.close()
        return

    writer.write(b'HTTP/1.1 200 Connection Established\\r\\n\\r\\n')
    await writer.drain()

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        tune_socket(ssh_sock)

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
    writer.write(b'HTTP/1.1 101 Switching Protocols\\r\\n')
    writer.write(b'Upgrade: websocket\\r\\n')
    writer.write(b'Connection: Upgrade\\r\\n')
    writer.write(b'\\r\\n')
    await writer.drain()

    ssh_reader, ssh_writer = None, None
    for attempt in range(3):
        try:
            ssh_reader, ssh_writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', DROPBEAR_PORT, limit=STREAM_LIMIT),
                timeout=3.0
            )
            break
        except Exception as e:
            logging.warning(f"Dropbear attempt {{attempt+1}}/3 failed: {{e}}")
            if attempt < 2:
                await asyncio.sleep(0.5)
    if not ssh_reader:
        logging.error("Could not connect to Dropbear")
        writer.close()
        return

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        tune_socket(ssh_sock)

    logging.info(f"WebSocket tunnel established")
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

async def main():
    server = await asyncio.start_server(
        handle_client, BIND_HOST, PROXY_PORT,
        limit=STREAM_LIMIT,
        backlog=1024,
        reuse_address=True,
    )
    for s in server.sockets:
        tune_socket(s)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    logging.info(f"Proxy listening on {{BIND_HOST}}:{{PROXY_PORT}} (uvloop) – WebSocket, CONNECT, and HTTP forward")
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    if hasattr(uvloop, 'run'):
        uvloop.run(main())
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        asyncio.run(main())
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        service_content = f"""[Unit]
Description=Universal Proxy (WebSocket + CONNECT + HTTP)
After=network.target dropbear-tunnel.service
Wants=dropbear-tunnel.service

[Service]
Type=simple
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

        result = Shell.run(f"systemctl start {SERVICE_NAME}", check=False, timeout=30)
        if not result.ok:
            log.error(f"Proxy start failed: {result.stderr}")
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 20", check=False)
            raise Exception("Proxy failed to start")

        time.sleep(2)
        status = Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False, timeout=5)
        if not status.ok or "active" not in status.stdout:
            log.error(f"Proxy is not active: {status.stdout}")
            raise Exception("Proxy not active")

        log.success(f"Universal proxy installed on port {proxy_port} (WebSocket, CONNECT, HTTP forward)")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False, timeout=10)
        Path(f"/etc/systemd/system/{SERVICE_NAME}").unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", timeout=10)
        log.info("Python Proxy removed")

    def _ensure_uvloop(self) -> None:
        """uvloop is mandatory: try pip (new + old pip syntax), then apt."""
        def have() -> bool:
            return Shell.run("python3 -c 'import uvloop'", check=False, timeout=20).ok
        if have():
            return
        log.info("Installing uvloop...")
        for cmd in ("pip3 install -q uvloop --break-system-packages",
                    "pip3 install -q uvloop",
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-uvloop"):
            Shell.run(cmd, check=False, timeout=300)
            if have():
                log.success("uvloop installed.")
                return
        raise Exception("uvloop could not be installed (tried pip and apt)")

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return True
            except OSError:
                return False
