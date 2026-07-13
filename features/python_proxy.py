"""
Python asyncio websocket-to-TCP proxy.
Listens on 127.0.0.1:<proxy_port>, handles WebSocket upgrade,
then forwards raw bytes to dropbear on 127.0.0.1:<dropbear_port>.
"""
from __future__ import annotations

from pathlib import Path

from core.config import APP_ROOT, DROPBEAR_PORT_DEFAULT, PROXY_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PROXY_SCRIPT = APP_ROOT / "proxy" / "ws_proxy.py"
PROXY_SERVICE = "/etc/systemd/system/sshauto-proxy.service"
PROXY_LOG = "/var/log/sshauto/proxy.log"


class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    description = "Install the asyncio WebSocket-to-TCP relay"
    depends_on = ["packages", "dropbear_service"]

    def is_installed(self) -> bool:
        return PROXY_SCRIPT.exists() and Path(PROXY_SERVICE).exists()

    def install(self) -> None:
        data = state.ensure_defaults()
        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)

        # Ensure log directory exists
        Path(PROXY_LOG).parent.mkdir(parents=True, exist_ok=True)

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
StandardOutput=append:{PROXY_LOG}
StandardError=append:{PROXY_LOG}

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
then forwards de-framed RFC6455 payloads to 127.0.0.1:{dropbear_port}.
"""
import asyncio
import struct
import sys
import base64
from hashlib import sha1

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

async def read_exact(reader, buf: bytearray, n: int):
    """Ensure buf holds at least n bytes, pulling more off the socket as needed."""
    while len(buf) < n:
        chunk = await reader.read(max(4096, n - len(buf)))
        if not chunk:
            raise ConnectionError("peer closed mid-frame")
        buf.extend(chunk)

async def read_ws_frame(reader, buf: bytearray):
    """
    Parse exactly one RFC6455 frame (pulling more bytes from reader as
    needed), unmasking the payload if the client set the MASK bit.
    Returns (opcode, payload); leftover bytes stay buffered for next call.
    """
    await read_exact(reader, buf, 2)
    b0, b1 = buf[0], buf[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    header_len = 2

    if length == 126:
        await read_exact(reader, buf, 4)
        length = struct.unpack("!H", bytes(buf[2:4]))[0]
        header_len = 4
    elif length == 127:
        await read_exact(reader, buf, 10)
        length = struct.unpack("!Q", bytes(buf[2:10]))[0]
        header_len = 10

    mask_key = b""
    if masked:
        await read_exact(reader, buf, header_len + 4)
        mask_key = bytes(buf[header_len:header_len + 4])
        header_len += 4

    await read_exact(reader, buf, header_len + length)
    payload = bytes(buf[header_len:header_len + length])
    del buf[:header_len + length]

    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    return opcode, payload

def build_ws_frame(payload: bytes, opcode: int = 0x2) -> bytes:
    """Build a single unmasked server->client frame (FIN=1)."""
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack("!H", n)
    else:
        header += bytes([127]) + struct.pack("!Q", n)
    return header + payload

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

        async def client_to_dropbear():
            buf = bytearray()
            try:
                while True:
                    opcode, payload = await read_ws_frame(reader, buf)
                    if opcode == 0x8:          # close
                        break
                    elif opcode == 0x9:        # ping -> pong
                        writer.write(build_ws_frame(payload, opcode=0xA))
                        await writer.drain()
                    elif opcode == 0xA:        # pong, ignore
                        pass
                    elif payload:               # text/binary/continuation = tunnel data
                        db_writer.write(payload)
                        await db_writer.drain()
            except Exception:
                pass
            finally:
                db_writer.close()

        async def dropbear_to_client():
            try:
                while True:
                    data = await db_reader.read(8192)
                    if not data:
                        break
                    writer.write(build_ws_frame(data))
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        await asyncio.gather(client_to_dropbear(), dropbear_to_client())

    except Exception:
        pass
    finally:
        writer.close()

async def main():
    server = await asyncio.start_server(handle_client, '127.0.0.1', PROXY_PORT)
    print(f"[+] WebSocket proxy running on 127.0.0.1:{{PROXY_PORT}} -> dropbear:{{DROPBEAR_PORT}}")
    await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
