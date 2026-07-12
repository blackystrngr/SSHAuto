#!/usr/bin/env python3
"""
WebSocket‑to‑TCP proxy with proper WebSocket framing.
Accepts HTTP upgrade, computes correct Sec-WebSocket-Accept,
then decodes/encodes WebSocket frames.
"""
import argparse
import base64
import hashlib
import logging
import socket
import select
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("wsproxy")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

BUFLEN = 4096 * 4
IDLE_TIMEOUT_ROUNDS = 60
SELECT_TIMEOUT = 3
INITIAL_READ_TIMEOUT = 20
HEADER_SETTLE_TIMEOUT = 0.3
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def compute_websocket_accept(key: str) -> str:
    """Compute the Sec-WebSocket-Accept value."""
    combined = key.strip() + WEBSOCKET_GUID
    sha1 = hashlib.sha1(combined.encode()).digest()
    return base64.b64encode(sha1).decode()


def parse_headers(raw: bytes) -> dict:
    """Parse HTTP headers into a dict."""
    headers = {}
    lines = raw.split(b"\r\n")
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, val = line.split(b":", 1)
        headers[key.lower().decode().strip()] = val.decode().strip()
    return headers


def decode_websocket_frame(data: bytes):
    """
    Decode a WebSocket frame.
    Returns (opcode, payload, remaining_data, is_final).
    """
    if len(data) < 2:
        return None, None, data, False
    byte1, byte2 = data[0], data[1]
    fin = (byte1 & 0x80) != 0
    opcode = byte1 & 0x0F
    masked = (byte2 & 0x80) != 0
    payload_len = byte2 & 0x7F
    idx = 2
    if payload_len == 126:
        if len(data) < 4:
            return None, None, data, False
        payload_len = struct.unpack('>H', data[idx:idx+2])[0]
        idx += 2
    elif payload_len == 127:
        if len(data) < 10:
            return None, None, data, False
        payload_len = struct.unpack('>Q', data[idx:idx+8])[0]
        idx += 8
    if masked:
        if len(data) < idx + 4:
            return None, None, data, False
        mask = data[idx:idx+4]
        idx += 4
    else:
        mask = None
    if len(data) < idx + payload_len:
        return None, None, data, False
    payload = data[idx:idx+payload_len]
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    remaining = data[idx+payload_len:]
    return opcode, payload, remaining, fin


def encode_websocket_frame(payload: bytes, opcode: int = 0x2) -> bytes:
    """Encode a WebSocket frame (unmasked, server -> client)."""
    frame = bytearray()
    frame.append(0x80 | opcode)  # FIN + opcode
    length = len(payload)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(struct.pack('>H', length))
    else:
        frame.append(127)
        frame.extend(struct.pack('>Q', length))
    frame.extend(payload)
    return bytes(frame)


@dataclass
class ProxySettings:
    listen_host: str = "127.0.0.1"
    listen_port: int = 109
    default_backend_host: str = "127.0.0.1"
    default_backend_port: int = 143
    shared_pass: Optional[str] = None


class ConnectionHandler(threading.Thread):
    def __init__(self, client_sock: socket.socket, addr, settings: ProxySettings):
        super().__init__(daemon=True)
        self.client = client_sock
        self.addr = addr
        self.settings = settings
        self.target: Optional[socket.socket] = None

    def _read_headers(self) -> bytes:
        raw = b""
        self.client.settimeout(INITIAL_READ_TIMEOUT)
        try:
            chunk = self.client.recv(BUFLEN)
        except (socket.timeout, OSError):
            return b""
        if not chunk:
            return b""
        raw += chunk
        self.client.settimeout(HEADER_SETTLE_TIMEOUT)
        while len(raw) < BUFLEN and not (b"\r\n\r\n" in raw or b"\n\n" in raw):
            try:
                chunk = self.client.recv(BUFLEN - len(raw))
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            raw += chunk
        self.client.settimeout(None)
        return raw

    def _safe_send(self, sock: socket.socket, data: bytes):
        try:
            sock.sendall(data)
        except OSError:
            pass

    def run(self):
        try:
            raw = self._read_headers()
            if not raw:
                return

            headers = parse_headers(raw)
            upgrade = headers.get("upgrade", "").lower()
            if upgrade != "websocket":
                # Not a WebSocket upgrade – close politely
                self._safe_send(self.client, b"HTTP/1.1 400 Bad Request\r\n\r\n")
                self.client.close()
                return

            key = headers.get("sec-websocket-key", "")
            if not key:
                self._safe_send(self.client, b"HTTP/1.1 400 Bad Request\r\n\r\n")
                self.client.close()
                return

            accept = compute_websocket_accept(key)
            response = (
                f"HTTP/1.1 101 Switching Protocols\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                f"\r\n"
            ).encode()
            self._safe_send(self.client, response)

            # Connect to backend (dropbear)
            try:
                self.target = socket.create_connection(
                    (self.settings.default_backend_host, self.settings.default_backend_port),
                    timeout=10
                )
            except OSError as e:
                logger.warning("backend connect failed: %s", e)
                self.client.close()
                return

            logger.info("WebSocket tunnel open: %s -> %s:%s",
                        self.addr, self.settings.default_backend_host, self.settings.default_backend_port)

            # Relay with WebSocket framing
            self._relay_ws()

        except Exception as e:
            logger.debug("connection error from %s: %s", self.addr, e)
        finally:
            self._close_all()

    def _relay_ws(self):
        """Relay data with WebSocket framing."""
        client = self.client
        backend = self.target
        sockets = [client, backend]
        idle_rounds = 0
        # Buffer for partial WebSocket frames
        read_buffer = b""

        while True:
            try:
                rlist, _, _ = select.select(sockets, [], sockets, SELECT_TIMEOUT)
            except (OSError, ValueError):
                break
            if not rlist:
                idle_rounds += 1
                if idle_rounds >= IDLE_TIMEOUT_ROUNDS:
                    break
                continue
            idle_rounds = 0

            for sock in rlist:
                if sock is client:
                    # Read from WebSocket client
                    try:
                        data = sock.recv(BUFLEN)
                    except OSError:
                        return
                    if not data:
                        return
                    read_buffer += data
                    # Process all complete frames
                    while True:
                        opcode, payload, remaining, fin = decode_websocket_frame(read_buffer)
                        if opcode is None:
                            # Need more data
                            break
                        if opcode == 0x8:  # Close frame
                            # Send close frame back
                            self._safe_send(client, encode_websocket_frame(payload, opcode=0x8))
                            return
                        if opcode == 0x2 or opcode == 0x1:  # binary or text
                            # Forward payload to backend
                            try:
                                backend.sendall(payload)
                            except OSError:
                                return
                        read_buffer = remaining

                elif sock is backend:
                    # Read from backend (dropbear) and send as WebSocket frame
                    try:
                        data = sock.recv(BUFLEN)
                    except OSError:
                        return
                    if not data:
                        return
                    # Send as a binary WebSocket frame (opcode 0x2)
                    frame = encode_websocket_frame(data, opcode=0x2)
                    try:
                        client.sendall(frame)
                    except OSError:
                        return

    def _close_all(self):
        for s in (self.client, self.target):
            if s is None:
                continue
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


class ProxyServer:
    def __init__(self, settings: ProxySettings):
        self.settings = settings
        self._sock: Optional[socket.socket] = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.settings.listen_host, self.settings.listen_port))
        self._sock.listen(128)

        logger.info(
            "wsproxy listening on %s:%s, backend %s:%s (WebSocket)",
            self.settings.listen_host, self.settings.listen_port,
            self.settings.default_backend_host, self.settings.default_backend_port,
        )

        try:
            while True:
                client, addr = self._sock.accept()
                handler = ConnectionHandler(client, addr, self.settings)
                handler.start()
        except KeyboardInterrupt:
            pass
        finally:
            self._sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:109", help="listen address:port")
    parser.add_argument("--backend", default="127.0.0.1:143", help="backend address:port")
    parser.add_argument("--shared-pass", help="optional shared password (not used in this version)")
    args = parser.parse_args()

    listen_host, listen_port_str = args.listen.split(":")
    backend_host, backend_port_str = args.backend.split(":")

    settings = ProxySettings(
        listen_host=listen_host,
        listen_port=int(listen_port_str),
        default_backend_host=backend_host,
        default_backend_port=int(backend_port_str),
        shared_pass=args.shared_pass,
    )
    ProxyServer(settings).start()


if __name__ == "__main__":
    main()
