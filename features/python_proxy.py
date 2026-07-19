import os
import socket
import time
from pathlib import Path

from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state, PROXY_PORT_DEFAULT, DROPBEAR_PORT_DEFAULT, LOG_DIR

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

        # Force install uvloop – mandatory for performance
        Shell.run("pip3 install uvloop --break-system-packages", check=False, timeout=30)

        proxy_code = f'''#!/usr/bin/env python3
import asyncio
import socket
import logging
import time
import sys
from pathlib import Path

# --- FORCE uvloop (fastest event loop) ---
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    uvloop_used = True
except ImportError:
    uvloop_used = False
    logging.warning("uvloop not installed – falling back to asyncio")

LOG_DIR = Path("/var/log/sshauto")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "proxy.log"),
    level=logging.WARNING,   # reduced to avoid disk I/O
    format='%(asctime)s %(levelname)s %(message)s'
)
logging.info(f"Proxy starting, uvloop: {{uvloop_used}}")

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}
READ_CHUNK = 262144  # 256KB – reduces syscalls

def tune_socket(sock):
    """Apply the most aggressive low‑latency socket options."""
    try:
        # 1. Disable Nagle – send immediately
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 2. Enable quick ACKs
        sock.setsockopt(socket.IPPROTO_TCP, 12, 1)  # TCP_QUICKACK
        # 3. Huge buffers – prevent backpressure
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4194304)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4194304)
        # 4. Aggressive keepalive – keep NAT alive
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        # 5. Busy‑poll – reduces wake‑up latency (if kernel supports)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BUSY_POLL, 50)
        except Exception:
            pass
    except Exception:
        pass

async def relay(a_reader, a_writer, b_reader, b_writer):
    """
    Bidirectional pump – tears down instantly when one side finishes.
    Uses FIRST_COMPLETED instead of waiting for both, cutting latency.
    """
    async def pump(src, dst):
        try:
            while True:
                data = await src.read(READ_CHUNK)
                if not data:
                    return
                dst.write(data)
                await dst.drain()
        except Exception:
            return

    t1 = asyncio.ensure_future(pump(a_reader, b_writer))
    t2 = asyncio.ensure_future(pump(b_reader, a_writer))
    _, pending = await asyncio.wait({{t1, t2}}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    # Drain cancellations so they don't linger in the loop (avoids GC warnings
    # and shaves a tiny bit of scheduler overhead under high connection churn)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for w in (a_writer, b_writer):
        try:
            w.close()
        except Exception:
            pass

async def handle_client(reader, writer):
    start_time = time.time()
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

    parts = first_line.decode().strip().split()
    if len(parts) < 3:
        writer.write(b'HTTP/1.1 400 Bad Request\\r\\n\\r\\n')
        writer.close()
        return
    method, raw_target, version = parts[0], parts[1], parts[2]

    headers = {{}}
    while True:
        line = await reader.readline()
        if not line or line == b'\\r\\n':
            break
        key, value = line.decode().strip().split(':', 1)
        headers[key.lower()] = value.strip()

    upgrade = headers.get('upgrade', '').lower()
    if upgrade == 'websocket':
        logging.info(f"WebSocket upgrade from {{peername}} (method: {{method}})")
        await handle_websocket(reader, writer)
        return

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

    writer.write(b'HTTP/1.1 405 Method Not Allowed\\r\\n\\r\\n')
    writer.close()
    logging.info(f"Unsupported request from {{peername}}")

async def handle_connect(reader, writer, host, port):
    try:
        ssh_reader, ssh_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0
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
        await relay(reader, writer, ssh_reader, ssh_writer)
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

    ssh_reader = ssh_writer = None
    for attempt in range(3):
        try:
            ssh_reader, ssh_writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', DROPBEAR_PORT),
                timeout=2.0
            )
            break
        except Exception as e:
            logging.warning(f"Dropbear connection attempt {{attempt+1}}/3 failed: {{e}}")
            if attempt < 2:
                await asyncio.sleep(0.5)
    if not ssh_reader:
        logging.error("Could not connect to Dropbear after 3 attempts")
        writer.close()
        return

    ssh_sock = ssh_writer.get_extra_info('socket')
    if ssh_sock:
        tune_socket(ssh_sock)

    logging.info(f"WebSocket tunnel established")
    try:
        await relay(reader, writer, ssh_reader, ssh_writer)
    except Exception as e:
        logging.error(f"WebSocket tunnel error: {{e}}")
    finally:
        writer.close()
        ssh_writer.close()

async def main():
    server = await asyncio.start_server(
        handle_client, '127.0.0.1', PROXY_PORT,
        backlog=1024,  # large queue for many concurrent connections
        reuse_address=True,
    )
    for s in server.sockets:
        tune_socket(s)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Enable TCP Fast Open on the listening socket (queue size 256)
            s.setsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_FASTOPEN", 23), 256)
        except Exception:
            pass

    logging.info(f"Proxy listening on 127.0.0.1:{{PROXY_PORT}} (uvloop: {{uvloop_used}})")
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
        PROXY_BIN.write_text(proxy_code)
        PROXY_BIN.chmod(0o755)

        # Systemd service – ultra‑high priority, safe for single‑vCPU
        service_content = f"""[Unit]
Description=Unified Proxy (WebSocket + CONNECT)
After=network.target dropbear-tunnel.service
Wants=dropbear-tunnel.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {PROXY_BIN}
Restart=always
RestartSec=1
User=root
Nice=-10
CPUSchedulingPolicy=rr
CPUSchedulingPriority=50
IOSchedulingClass=best-effort
IOSchedulingPriority=0
LimitNOFILE=1048576
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

        log.success(f"Proxy installed on port {proxy_port} (ultra‑fast, uvloop, real‑time)")

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
