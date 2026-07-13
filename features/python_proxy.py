"""
Python asyncio websocket-to-TCP proxy with full framing.
Listens on 127.0.0.1:<proxy_port>, handles WebSocket upgrade,
then forwards WebSocket payloads to dropbear and frames responses back.
"""
from __future__ import annotations

import asyncio
import base64
import struct
from hashlib import sha1
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

    def _proxy_code(self, dropbear_port: int, proxy_port: int) -> str:
        return f'''#!/usr/bin/env python3
"""
WebSocket-to-TCP proxy with full frame handling.
Listens on 127.0.0.1:{proxy_port}, forwards WebSocket payloads to dropbear:{dropbear_port}.
"""
import asyncio
import base64
import struct
import sys
from hashlib import sha1
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='{PROXY_LOG}',
    filemode='a'
)
log = logging.getLogger("ws_proxy")

DROPBEAR_PORT = {dropbear_port}
PROXY_PORT = {proxy_port}

# ---------- WebSocket frame helpers ----------
def decode_frame(data):
    """Parse a WebSocket frame, return (opcode, payload, remaining_data)."""
    if len(data) < 2:
        return None, None, data
    b1, b2 = data[0], data[1]
    fin = (b1 & 0x80) != 0
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F
    offset = 2
    if payload_len == 126:
        if len(data) < 4:
            return None, None, data
        payload_len = struct.unpack(">H", data[2:4])[0]
        offset = 4
    elif payload_len == 127:
        if len(data) < 10:
            return None, None, data
        payload_len = struct.unpack(">Q", data[2:10])[0]
        offset = 10
    if masked:
        if len(data) < offset + 4:
            return None, None, data
        mask = data[offset:offset+4]
        offset += 4
    else:
        mask = None
    if len(data) < offset + payload_len:
        return None, None, data  # incomplete frame
    payload = data[offset:offset+payload_len]
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    remaining = data[offset+payload_len:]
    return opcode, payload, remaining

def encode_frame(opcode, payload, mask=False):
    """Encode a WebSocket frame (server->client, unmasked by default)."""
    b1 = 0x80 | (opcode & 0x0F)  # fin=1
    payload_len = len(payload)
    header = bytearray()
    header.append(b1)
    if payload_len <= 125:
        header.append((0x80 if mask else 0x00) | payload_len)
    elif payload_len <= 65535:
        header.append((0x80 if mask else 0x00) | 126)
        header.extend(struct.pack(">H", payload_len))
    else:
        header.append((0x80 if mask else 0x00) | 127)
        header.extend(struct.pack(">Q", payload_len))
    if mask:
        mask_key = b'\\x00\\x01\\x02\\x03'
        header.extend(mask_key)
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + payload

def parse_headers(data):
    lines = data.decode('utf-8', errors='ignore').split('\\r\\n')
    headers = {{}}
    for line in lines[1:]:
        if ': ' in line:
            k, v = line.split(': ', 1)
            headers[k.lower()] = v
    return headers

# ---------- proxy logic ----------
async def pipe_raw(src_reader, dst_writer):
    """Blind byte pump, used for non-WebSocket (direct) connections."""
    try:
        while True:
            data = await src_reader.read(8192)
            if not data:
                break
            dst_writer.write(data)
            await dst_writer.drain()
    except Exception as e:
        log.error(f"pipe_raw error: {{e}}")

async def handle_client(reader, writer):
    db_writer = None
    try:
        # Read until we have the full header block -- a single recv() is not
        # guaranteed to contain the whole HTTP upgrade request.
        request = b''
        while b'\\r\\n\\r\\n' not in request:
            chunk = await reader.read(8192)
            if not chunk:
                return
            request += chunk
            if len(request) > 65536:
                writer.write(b"HTTP/1.1 431 Request Header Fields Too Large\\r\\n\\r\\n")
                await writer.drain()
                return
            if len(request) >= 16 and not (b'\\r\\n\\r\\n' in request) and b'\\n' not in request[:16] and not request[:4].isupper():
                # Doesn't look like it's building toward HTTP headers at all
                # (e.g. raw SSH banner) -- stop waiting and treat as direct.
                break

        header_end = request.find(b'\\r\\n\\r\\n')

        if header_end == -1 or request.startswith(b'SSH-'):
            # No HTTP header block found, or this is a raw SSH client
            # connecting directly -- pure TCP passthrough, no response sent.
            try:
                db_reader, db_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
            except Exception as e:
                log.error(f"Can't connect to dropbear (direct): {{e}}")
                writer.close()
                return
            if request:
                db_writer.write(request)
                await db_writer.drain()
            log.info(f"direct tunnel open from {{writer.get_extra_info('peername')}}")
            await asyncio.gather(pipe_raw(reader, db_writer), pipe_raw(db_reader, writer))
            return

        header_end += 4
        headers = parse_headers(request[:header_end])
        leftover = request[header_end:]

        if headers.get('upgrade', '').lower() != 'websocket':
            # HTTP-looking but not a WS upgrade -- pass through raw too.
            try:
                db_reader, db_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
            except Exception as e:
                log.error(f"Can't connect to dropbear (direct): {{e}}")
                writer.close()
                return
            if request:
                db_writer.write(request)
                await db_writer.drain()
            await asyncio.gather(pipe_raw(reader, db_writer), pipe_raw(db_reader, writer))
            return

        # ---- real WebSocket upgrade ----
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
        log.info(f"WebSocket handshake accepted from {{writer.get_extra_info('peername')}}")

        try:
            db_reader, db_writer = await asyncio.open_connection('127.0.0.1', DROPBEAR_PORT)
        except Exception as e:
            log.error(f"Can't connect to dropbear: {{e}}")
            writer.close()
            return

        buffer = bytearray(leftover)  # keep any frame bytes that arrived with the handshake

        async def ws_to_tcp():
            nonlocal buffer
            while True:
                try:
                    if not buffer:
                        data = await reader.read(8192)
                        if not data:
                            break
                        buffer += data
                    while True:
                        opcode, payload, remaining = decode_frame(bytes(buffer))
                        if opcode is None:
                            break  # incomplete frame, wait for more
                        buffer = bytearray(remaining)
                        if opcode == 0x08:                    # close
                            return
                        elif opcode == 0x09:                  # ping -> pong
                            writer.write(encode_frame(0x0A, payload))
                            await writer.drain()
                        elif opcode == 0x0A:                  # pong, ignore
                            pass
                        elif opcode in (0x00, 0x01, 0x02):    # continuation/text/binary all carry stream data
                            db_writer.write(payload)
                            await db_writer.drain()
                except Exception as e:
                    log.error(f"ws_to_tcp error: {{e}}")
                    break

        async def tcp_to_ws():
            while True:
                try:
                    data = await db_reader.read(8192)
                    if not data:
                        break
                    frame = encode_frame(0x02, data, mask=False)
                    writer.write(frame)
                    await writer.drain()
                except Exception as e:
                    log.error(f"tcp_to_ws error: {{e}}")
                    break

        await asyncio.gather(ws_to_tcp(), tcp_to_ws())

    except Exception as e:
        log.error(f"handle_client exception: {{e}}")
    finally:
        writer.close()
        if db_writer is not None:
            try:
                db_writer.close()
            except Exception:
                pass
        log.info("Connection closed")

async def main():
    server = await asyncio.start_server(handle_client, '127.0.0.1', PROXY_PORT)
    log.info(f"Proxy listening on 127.0.0.1:{{PROXY_PORT}} -> dropbear:{{DROPBEAR_PORT}}")
    await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
'''
