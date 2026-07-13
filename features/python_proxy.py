"""
Manages the extreme low-latency Python Asyncio WebSocket proxy infrastructure service.
"""
from __future__ import annotations

import os
from pathlib import Path
from core.config import APP_ROOT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PROXY_SCRIPT_PATH = APP_ROOT / "ws_proxy.py"
SYSTEMD_UNIT_PATH = Path("/etc/systemd/system/sshauto-proxy.service")


class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    description = "Deploy performance-tuned WebSocket to Dropbear asyncio engine"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return PROXY_SCRIPT_PATH.exists() and SYSTEMD_UNIT_PATH.exists()

    def install(self) -> None:
        data = state.ensure_defaults()
        proxy_port = data.get("proxy_port", 8000)
        dropbear_port = data.get("dropbear_port", 110)

        APP_ROOT.mkdir(parents=True, exist_ok=True)

        script_content = f"""#!/usr/bin/env python3
import asyncio
import socket
import hashlib
import base64

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

BUFFER_SIZE = 65536

def make_ws_frame(data: bytes) -> bytes:
    b1 = 0x82
    length = len(data)
    if length < 126:
        header = bytes([b1, length])
    elif length <= 0xFFFF:
        header = bytes([b1, 126]) + length.to_bytes(2, byteorder='big')
    else:
        header = bytes([b1, 127]) + length.to_bytes(8, byteorder='big')
    return header + data

async def read_ws_frame(reader: asyncio.StreamReader) -> bytes | None:
    try:
        header = await reader.readexactly(2)
        if not header:
            return None
        b1, b2 = header[0], header[1]
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        payload_len = b2 & 0x7F

        if payload_len == 126:
            ext = await reader.readexactly(2)
            payload_len = int.from_bytes(ext, byteorder='big')
        elif payload_len == 127:
            ext = await reader.readexactly(8)
            payload_len = int.from_bytes(ext, byteorder='big')

        mask_key = await reader.readexactly(4) if masked else None
        payload = await reader.readexactly(payload_len)

        if opcode == 0x08:
            return None

        if mask_key:
            payload = bytearray(payload)
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]
            payload = bytes(payload)
        return payload
    except Exception:
        return None

async def pipe_ws_to_tcp(ws_reader, tcp_writer):
    try:
        while True:
            payload = await read_ws_frame(ws_reader)
            if payload is None:
                break
            if payload:
                tcp_writer.write(payload)
                await tcp_writer.drain()
    except Exception:
        pass

async def pipe_tcp_to_ws(tcp_reader, ws_writer):
    try:
        while True:
            data = await tcp_reader.read(BUFFER_SIZE)
            if not data:
                break
            ws_writer.write(make_ws_frame(data))
            await ws_writer.drain()
    except Exception:
        pass

async def handle_handshake(reader, writer) -> bool:
    try:
        header_bytes = b""
        # Tolerant of both standard \\r\\n\\r\\n and malformed \\n\\n injection payloads
        while b"\\r\\n\\r\\n" not in header_bytes and b"\\n\\n" not in header_bytes:
            chunk = await reader.read(4096)
            if not chunk:
                return False
            header_bytes += chunk
            if len(header_bytes) > 16384:
                return False

        ws_key = None
        lines = header_bytes.replace(b"\\r", b"").split(b"\\n")
        for line in lines:
            if line.lower().startswith(b"sec-websocket-key:"):
                ws_key = line.split(b":", 1)[1].strip().decode()
                break

        if ws_key:
            guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept_raw = hashlib.sha1((ws_key + guid).encode()).digest()
            accept_str = base64.b64encode(accept_raw).decode()
            response = (
                "HTTP/1.1 101 Switching Protocols\\r\\n"
                "Upgrade: websocket\\r\\n"
                "Connection: Upgrade\\r\\n"
                f"Sec-WebSocket-Accept: {{accept_str}}\\r\\n\\r\\n"
            )
        else:
            # Fallback for HTTP Injectors that don't send a standard WS Key
            response = (
                "HTTP/1.1 101 Switching Protocols\\r\\n"
                "Upgrade: websocket\\r\\n"
                "Connection: Upgrade\\r\\n\\r\\n"
            )

        writer.write(response.encode())
        await writer.drain()
        return True
    except Exception:
        return False

async def main_handler(client_reader, client_writer):
    client_sock = client_writer.get_extra_info('socket')
    if client_sock:
        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    if not await handle_handshake(client_reader, client_writer):
        client_writer.close()
        return

    try:
        db_reader, db_writer = await asyncio.open_connection('127.0.0.1', {dropbear_port})
        db_sock = db_writer.get_extra_info('socket')
        if db_sock:
            db_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        client_writer.close()
        return

    t1 = asyncio.create_task(pipe_ws_to_tcp(client_reader, db_writer))
    t2 = asyncio.create_task(pipe_tcp_to_ws(db_reader, client_writer))

    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    
    for task in [t1, t2]:
        if not task.done():
            task.cancel()

    for w in [client_writer, db_writer]:
        try:
            w.close()
        except Exception:
            pass

async def main():
    server = await asyncio.start_server(main_handler, '127.0.0.1', {proxy_port}, backlog=256)
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
"""
        PROXY_SCRIPT_PATH.write_text(script_content)
        PROXY_SCRIPT_PATH.chmod(0o755)

        unit_content = f"""[Unit]
Description=SSHAuto Automated WebSocket High-Performance Proxy Core
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 {PROXY_SCRIPT_PATH}
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""
        SYSTEMD_UNIT_PATH.write_text(unit_content)
        
        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable sshauto-proxy")
        Shell.run("systemctl restart sshauto-proxy")
        log.success("Performance optimized proxy build actively handling system workflows.")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("systemctl disable sshauto-proxy", check=False)
        if SYSTEMD_UNIT_PATH.exists():
            SYSTEMD_UNIT_PATH.unlink()
        if PROXY_SCRIPT_PATH.exists():
            PROXY_SCRIPT_PATH.unlink()
        Shell.run("systemctl daemon-reload", check=False)
