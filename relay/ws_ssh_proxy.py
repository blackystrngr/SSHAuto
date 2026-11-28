#!/usr/bin/env python3
"""sshauto relay: the one public listener. No nginx, no other reverse proxy.

It binds every relay port itself and works out, per connection, what the client is speaking:

    first byte 0x16 ................ TLS            -> handshake here, then treat what's inside as below
    "SSH-..." ...................... raw SSH        -> straight to Dropbear (plain or inside TLS)
    HTTP + "Upgrade: websocket" .... WebSocket      -> 101, then Dropbear. Raw-after-101 (NetMod, HTTP
                                                       Custom, ...) and real RFC 6455 framing are both
                                                       detected and served.
    HTTP "CONNECT host:port" ....... SSH tunnel     -> only this server's own address (-> Dropbear/sshd);
                                                       any other target is refused (no open proxy)
    HTTP, path under panel secret .. web panel      -> reverse-proxied to the loopback panel server
    HTTP for this server ........... small 200 page (no request loops)
    HTTP for another host .......... same small 200 page (nothing is ever forwarded outward)
    silent client .................. raw SSH (server-first clients)

Every path that reaches a shell ends at Dropbear/sshd, so SSH account authentication is mandatory.
The relay never opens a connection to a third-party host.

Every port accepts both TLS and plain, so "TLS on port 80" and "plain on 443" just work. Duplicate
headers (two Host: lines) are accepted: the first one wins and every line is forwarded unchanged.

Configuration is read live from /var/lib/sshauto/state.json (cert, panel, Dropbear port); only the
set of listening ports needs a restart. Standard library plus uvloop (required).
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import ipaddress
import json
import logging
import logging.handlers
import os
import socket
import ssl
import struct
import subprocess
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

try:
    import uvloop
except ImportError:                       # tests may run on the stock loop; the installer guarantees uvloop
    uvloop = None

STATE_PATH = Path(os.environ.get("SSHAUTO_STATE", "/var/lib/sshauto/state.json"))
LOG_FILE = Path(os.environ.get("SSHAUTO_RELAY_LOG", "/var/log/sshauto/proxy.log"))

DEFAULT_HTTP_PORTS = (80, 8080, 8880, 2052, 2082, 2086, 2095)
DEFAULT_HTTPS_PORTS = (443, 8443, 2053, 2083, 2087, 2096)
DEFAULT_DROPBEAR_PORT = 110
DEFAULT_PROXY_PORT = 9955

READ_CHUNK = 65536
STREAM_LIMIT = 262144
SNIFF_TIMEOUT = 2.0          # silent this long -> assume a client that waits for the server banner (SSH)
HEAD_TIMEOUT = 30.0          # request line + headers must arrive within this
TLS_TIMEOUT = 15.0
WS_PROBE = 0.3               # how long to wait for the client's first bytes after 101 (raw vs framed)
HTTP_IDLE = 300.0            # idle limit for plain HTTP forwarding / panel
MAX_HEADER_LINES = 100
MAX_HEAD_BYTES = 65536
MAX_FRAME = 4 * 1024 * 1024
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
FIREWALL_RECHECK = 60.0

CLOUDFLARE_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32")]

log = logging.getLogger("relay")


# ------------------------------------------------------------------------------- configuration
class Config:
    """state.json, re-read when it changes (checked at most every 2 s)."""

    def __init__(self, path: Path = STATE_PATH, http=DEFAULT_HTTP_PORTS, https=DEFAULT_HTTPS_PORTS):
        self.path = Path(path)
        self.default_http, self.default_https = tuple(http), tuple(https)
        self._data: dict = {}
        self._mtime = -1.0
        self._checked = 0.0

    def get(self) -> dict:
        now = time.monotonic()
        if now - self._checked < 2.0 and self._mtime != -1.0:
            return self._data
        self._checked = now
        try:
            mtime = self.path.stat().st_mtime
            if mtime != self._mtime:
                self._data = json.loads(self.path.read_text() or "{}")
                self._mtime = mtime
        except (OSError, ValueError):
            if self._mtime == -1.0:
                self._data = {}
        return self._data

    def ports(self) -> tuple[list[int], list[int]]:
        st = self.get()
        http = sorted({*self.default_http, *map(int, st.get("custom_http_ports", []))})
        https = sorted({*self.default_https, *map(int, st.get("custom_https_ports", []))})
        return http, https

    @property
    def dropbear_port(self) -> int:
        return int(self.get().get("dropbear_port", DEFAULT_DROPBEAR_PORT))

    @property
    def ssh_port(self) -> int:
        return int(self.get().get("ssh_port", 22))

    @property
    def domain(self) -> str:
        return (self.get().get("cert_domain") or "").strip().lower()

    @property
    def server_ip(self) -> str:
        ip = (self.get().get("server_ip") or "").strip()
        try:
            ipaddress.ip_address(ip)
            return ip
        except ValueError:
            return ""

    def panel(self):
        st = self.get()
        port, path = st.get("panel_port"), st.get("panel_path")
        if port and path:
            return int(port), "/" + str(path).strip("/")
        return None


class TlsContexts:
    """One server context, rebuilt when the certificate files change. Deliberately permissive:
    TLS 1.0+ (old Android stacks), no ALPN (so a client offering only h2 or a custom ALPN still
    completes the handshake), SNI ignored (any SNI gets the same certificate, which is what
    SNI-fronting clients rely on)."""

    def __init__(self, cfg: Config):
        self.cfg, self._ctx, self._key = cfg, None, None

    def get(self):
        st = self.cfg.get()
        cert, key = st.get("cert_fullchain_path"), st.get("cert_key_path")
        if not (cert and key):
            return None
        try:
            stamp = (cert, key, os.stat(cert).st_mtime, os.stat(key).st_mtime)
        except OSError:
            return None
        if stamp != self._key:
            try:
                self._ctx, self._key = self.build(cert, key), stamp
            except (OSError, ssl.SSLError) as exc:
                log.error("cannot load certificate %s: %s", cert, exc)
                return self._ctx
            log.warning("TLS certificate loaded: %s", cert)
        return self._ctx

    @staticmethod
    def build(cert: str, key: str) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (ValueError, AttributeError):
            pass
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        except ssl.SSLError:
            pass
        ctx.load_cert_chain(cert, key)
        return ctx


# ---------------------------------------------------------------------------------- helpers
def tune_socket(sock: socket.socket) -> None:
    for level, opt, val in (
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", -1), 20),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", -1), 10),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", -1), 4),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_USER_TIMEOUT", -1), 90000),
    ):
        if opt == -1:
            continue
        try:
            sock.setsockopt(level, opt, val)
        except OSError:
            pass


def norm_ip(addr) -> str:
    ip = addr[0] if isinstance(addr, tuple) else str(addr)
    return ip[7:] if ip.startswith("::ffff:") else ip


def is_cloudflare(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in CLOUDFLARE_NETS if n.version == a.version)


def split_hostport(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):                                   # [v6]:port
        host, _, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif value.count(":") == 1:
        host, _, port = value.partition(":")
    else:
        host, port = value, ""
    try:
        return host, int(port) if port else default_port
    except ValueError:
        return host, default_port


def _local_address(ip) -> bool:
    fam = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            s.bind((str(ip), 0))
        return True
    except OSError:
        return False


class Conn:
    """One accepted client connection."""

    def __init__(self, reader, writer, tls: bool, peer: str):
        self.reader, self.writer, self.tls, self.peer = reader, writer, tls, peer

    def reply(self, status: str, body: bytes = b"", ctype: str = "text/plain", head: bool = False) -> None:
        extra = b"" if not body else b"Content-Type: " + ctype.encode() + b"\r\n"
        self.writer.write(b"HTTP/1.1 " + status.encode() + b"\r\nContent-Length: " + str(len(body)).encode()
                          + b"\r\n" + extra + b"Connection: close\r\n\r\n" + (b"" if head else body))

    def close(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------------- pumping
async def pipe(src, dst, idle: float | None = None, eof: bool = False) -> None:
    try:
        while True:
            data = await (asyncio.wait_for(src.read(READ_CHUNK), idle) if idle else src.read(READ_CHUNK))
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("pipe ended: %r", exc)
    if eof:
        try:
            if dst.can_write_eof():
                dst.write_eof()
        except Exception:
            pass


async def _finish(tasks, writers) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for w in writers:
        try:
            w.close()
        except Exception:
            pass


async def tunnel(a_r, a_w, b_r, b_w, pump_ab=None, pump_ba=None) -> None:
    """Full-duplex tunnel: as soon as either direction ends, tear down both (no leaked sockets)."""
    tasks = [asyncio.ensure_future((pump_ab or pipe)(a_r, b_w)),
             asyncio.ensure_future((pump_ba or pipe)(b_r, a_w))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _finish(tasks, (a_w, b_w))


async def http_relay(c_r, c_w, t_r, t_w) -> None:
    """Request/response relay: the client may half-close after sending; we finish when the
    upstream's direction is done (or idles out)."""
    up = asyncio.ensure_future(pipe(c_r, t_w, HTTP_IDLE, eof=True))
    down = asyncio.ensure_future(pipe(t_r, c_w, HTTP_IDLE))
    try:
        await asyncio.wait([down], return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _finish([up, down], (c_w, t_w))


# ------------------------------------------------------------------- WebSocket (RFC 6455) framing
def looks_framed(buf: bytes) -> bool:
    """A client that really speaks WebSocket sends a masked text/binary frame first; a raw SSH
    tunnel always starts with 'SSH-'."""
    return len(buf) >= 2 and (buf[0] & 0x0F) in (1, 2) and bool(buf[1] & 0x80)


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()


def ws_frame(payload: bytes, opcode: int = 0x2) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


class FrameParser:
    """Incremental client->server frame parser. feed() returns [(opcode, payload)]."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        while True:
            b = self.buf
            if len(b) < 2:
                break
            opcode, masked, n, pos = b[0] & 0x0F, bool(b[1] & 0x80), b[1] & 0x7F, 2
            if n == 126:
                if len(b) < 4:
                    break
                n, pos = struct.unpack("!H", b[2:4])[0], 4
            elif n == 127:
                if len(b) < 10:
                    break
                n, pos = struct.unpack("!Q", b[2:10])[0], 10
            if n > MAX_FRAME:
                raise ValueError("frame too large")
            need = pos + (4 if masked else 0) + n
            if len(b) < need:
                break
            if masked:
                key = bytes(b[pos:pos + 4])
                body = bytearray(b[pos + 4:need])
                # unmask with big-int XOR: fast enough in pure Python
                if body:
                    mask = (key * (len(body) // 4 + 1))[:len(body)]
                    body = (int.from_bytes(body, "big") ^ int.from_bytes(mask, "big")).to_bytes(len(body), "big")
            else:
                body = bytes(b[pos:need])
            del self.buf[:need]
            out.append((opcode, bytes(body)))
        return out


async def ws_from_client(reader, ssh_w, ws_w, parser: FrameParser, initial: bytes) -> None:
    pending = initial
    try:
        while True:
            if pending:
                data, pending = pending, b""
            else:
                data = await reader.read(READ_CHUNK)
                if not data:
                    return
            for opcode, payload in parser.feed(data):
                if opcode in (0x0, 0x1, 0x2):
                    ssh_w.write(payload)
                elif opcode == 0x9:
                    ws_w.write(ws_frame(payload, 0xA))
                elif opcode == 0x8:
                    ws_w.write(ws_frame(payload[:2], 0x8))
                    await ws_w.drain()
                    return
            await ssh_w.drain()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("ws client side ended: %r", exc)


async def ws_to_client(ssh_r, ws_w) -> None:
    try:
        while True:
            data = await ssh_r.read(32768)
            if not data:
                try:
                    ws_w.write(ws_frame(struct.pack("!H", 1000), 0x8))
                    await ws_w.drain()
                except Exception:
                    pass
                return
            ws_w.write(ws_frame(data))
            await ws_w.drain()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("ws server side ended: %r", exc)


# ------------------------------------------------------------------------------------- relay
class Relay:
    def __init__(self, cfg: Config, firewall: bool = True):
        self.cfg = cfg
        self.tls = TlsContexts(cfg)
        self.firewall = firewall
        self._tasks: set = set()

    # ---- connection admission --------------------------------------------------------------
    async def sniff(self, loop, conn: socket.socket) -> str:
        fut = loop.create_future()
        fd = conn.fileno()
        loop.add_reader(fd, lambda: fut.done() or fut.set_result(None))
        try:
            await asyncio.wait_for(fut, SNIFF_TIMEOUT)
        except asyncio.TimeoutError:
            return "silent"
        finally:
            loop.remove_reader(fd)
        try:
            first = conn.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return "plain"
        except OSError:
            return "closed"
        if not first:
            return "closed"
        return "tls" if first[0] == 0x16 else "plain"

    async def admit(self, conn: socket.socket, addr) -> None:
        loop = asyncio.get_running_loop()
        try:
            conn.setblocking(False)
            tune_socket(conn)
            kind = await self.sniff(loop, conn)
            if kind == "closed":
                conn.close()
                return
            ctx = None
            if kind == "tls":
                ctx = self.tls.get()
                if ctx is None:
                    log.warning("TLS client but no usable certificate; dropping %s", norm_ip(addr))
                    conn.close()
                    return
            peer = norm_ip(addr)
            reader = asyncio.StreamReader(limit=STREAM_LIMIT, loop=loop)

            def handler(r, w):
                return self.serve(Conn(r, w, ctx is not None, peer), silent=(kind == "silent"))

            proto = asyncio.StreamReaderProtocol(reader, handler, loop=loop)
            await loop.connect_accepted_socket(lambda: proto, conn, ssl=ctx,
                                               ssl_handshake_timeout=TLS_TIMEOUT if ctx else None)
        except Exception as exc:
            log.debug("admit failed for %s: %r", addr, exc)
            try:
                conn.close()
            except Exception:
                pass

    async def accept_loop(self, lsock: socket.socket, label: str) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn, addr = await loop.sock_accept(lsock)
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                if exc.errno in (errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM):
                    log.error("accept on %s: %s (out of descriptors?)", label, exc)
                await asyncio.sleep(0.2)
                continue
            task = asyncio.ensure_future(self.admit(conn, addr))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # ---- request handling ------------------------------------------------------------------
    async def serve(self, c: Conn, silent: bool = False) -> None:
        """Every exit path releases the socket; a leak here ends in EMFILE."""
        try:
            if silent:
                await self.ssh_tunnel(c, b"")
                return
            try:
                first, raw, headers, multi = await self.read_head(c.reader)
            except asyncio.TimeoutError:
                return
            except (ValueError, asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            if not first:
                return
            if first.startswith(b"SSH-"):
                await self.ssh_tunnel(c, first)
                return
            await self.http(c, first, raw, headers, multi)
        except Exception as exc:
            log.debug("handler error from %s: %r", c.peer, exc)
        finally:
            c.close()

    async def read_head(self, reader):
        async def _read():
            first = await reader.readline()
            raw, headers, multi, size = [], {}, [], len(first)
            if not first or first.startswith(b"SSH-"):
                return first, raw, headers, multi
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                size += len(line)
                if len(raw) >= MAX_HEADER_LINES or size > MAX_HEAD_BYTES:
                    raise ValueError("header section too large")
                raw.append(line)
                key, sep, value = line.decode("latin-1").partition(":")
                if sep:
                    k, v = key.strip().lower(), value.strip()
                    headers.setdefault(k, v)
                    multi.append((k, v))
            return first, raw, headers, multi
        return await asyncio.wait_for(_read(), HEAD_TIMEOUT)

    async def http(self, c: Conn, first: bytes, raw, headers, multi) -> None:
        parts = first.decode("latin-1").split()
        if len(parts) < 2:
            c.reply("400 Bad Request")
            return
        method, target = parts[0].upper(), parts[1]

        # WebSocket upgrade: any method, any path, any Host (header values are matched loosely)
        if any(k == "upgrade" and "websocket" in v.lower() for k, v in multi):
            await self.websocket(c, headers)
            return
        if method == "CONNECT":
            await self.connect(c, target)
            return

        if target.startswith("/"):
            path = target.split("?", 1)[0]
        else:
            path = urlsplit(target).path or "/"

        panel = self.cfg.panel()
        if panel and (path == panel[1] or path.startswith(panel[1] + "/")):
            await self.panel(c, first, raw, headers, panel[0])
            return

        host_value = urlsplit(target).netloc if "://" in target else headers.get("host", "")
        if not host_value:
            c.reply("400 Bad Request")
            return
        # Own host or not, the answer is the same static page: this relay is not an HTTP proxy.
        c.reply("200 OK", b"OK\n", head=(method == "HEAD"))

    # ---- own-address detection -------------------------------------------------------------
    async def is_own(self, host: str) -> bool:
        h = host.strip("[]").lower().rstrip(".")
        if h in ("", "localhost", "ip6-localhost"):
            return True
        domain = self.cfg.domain
        if domain and (h == domain or h.endswith("." + domain)):
            return True
        if h == self.cfg.server_ip:
            return True
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            return False
        return ip.is_loopback or ip.is_unspecified or _local_address(ip)

    # ---- backends --------------------------------------------------------------------------
    async def open_backend(self, host: str, port: int, attempts: int = 1, timeout: float = 10.0):
        last = None
        for i in range(attempts):
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection(host, port, limit=STREAM_LIMIT), timeout)
                sock = w.get_extra_info("socket")
                if sock:
                    tune_socket(sock)
                return r, w
            except (OSError, asyncio.TimeoutError) as exc:
                last = exc
                if i + 1 < attempts:
                    await asyncio.sleep(0.25)
        raise last or OSError("connect failed")

    async def dropbear(self):
        return await self.open_backend("127.0.0.1", self.cfg.dropbear_port, attempts=3, timeout=3.0)

    async def ssh_tunnel(self, c: Conn, initial: bytes) -> None:
        try:
            r, w = await self.dropbear()
        except (OSError, asyncio.TimeoutError) as exc:
            log.error("cannot reach Dropbear: %r", exc)
            return
        if initial:
            w.write(initial)
        await tunnel(c.reader, c.writer, r, w)

    # ---- WebSocket -------------------------------------------------------------------------
    async def websocket(self, c: Conn, headers: dict) -> None:
        try:
            r, w = await self.dropbear()
        except (OSError, asyncio.TimeoutError) as exc:
            log.error("cannot reach Dropbear: %r", exc)
            c.reply("502 Bad Gateway")
            return
        lines = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade"]
        key = headers.get("sec-websocket-key")
        if key:
            lines.append("Sec-WebSocket-Accept: " + ws_accept(key))
        proto = headers.get("sec-websocket-protocol")
        if proto:
            lines.append("Sec-WebSocket-Protocol: " + proto.split(",")[0].strip())
        c.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await c.writer.drain()

        # raw after 101 (the usual SSH-over-WebSocket payloads) or real framing? An SSH client's
        # first bytes are "SSH-"; a framed client's are a masked frame header.
        first, eof = b"", False
        try:
            first = await asyncio.wait_for(c.reader.read(READ_CHUNK), WS_PROBE)
            eof = not first
        except asyncio.TimeoutError:
            pass
        if eof:
            w.close()
            return
        if first and looks_framed(first):
            parser = FrameParser()
            await tunnel(c.reader, c.writer, r, w,
                         pump_ab=lambda src, dst: ws_from_client(src, dst, c.writer, parser, first),
                         pump_ba=lambda src, dst: ws_to_client(src, dst))
            return
        if first:
            w.write(first)
        await tunnel(c.reader, c.writer, r, w)

    # ---- CONNECT ---------------------------------------------------------------------------
    async def connect(self, c: Conn, target: str) -> None:
        host, port = split_hostport(target, 443)
        if not (0 < port < 65536) or not host:
            c.reply("400 Bad Request")
            return
        if not await self.is_own(host):
            log.warning("refused CONNECT to %s:%s from %s (not this server)", host, port, c.peer)
            c.reply("403 Forbidden")
            return
        http_ports, https_ports = self.cfg.ports()
        relay_ports = {*http_ports, *https_ports, *DEFAULT_HTTP_PORTS, *DEFAULT_HTTPS_PORTS, DEFAULT_PROXY_PORT}
        if port in (self.cfg.ssh_port, 22):
            dest = ("127.0.0.1", port)
        elif port in relay_ports or port == self.cfg.dropbear_port:
            dest = ("127.0.0.1", self.cfg.dropbear_port)
        else:
            c.reply("403 Forbidden")
            return
        attempts = 3
        try:
            r, w = await self.open_backend(dest[0], dest[1], attempts=attempts)
        except (OSError, asyncio.TimeoutError) as exc:
            log.error("CONNECT %s:%s failed: %r", host, port, exc)
            c.reply("502 Bad Gateway")
            return
        c.writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await c.writer.drain()
        await tunnel(c.reader, c.writer, r, w)

    # ---- web panel (reverse proxy to the loopback panel server) ------------------------------
    async def panel(self, c: Conn, first: bytes, raw, headers: dict, port: int) -> None:
        proto = "https" if c.tls else headers.get("x-forwarded-proto", "http").split(",")[0].strip().lower()
        if proto != "https":
            c.reply("404 Not Found")          # never serve sign-in over plain HTTP
            return
        client = c.peer
        if is_cloudflare(c.peer):
            cf = headers.get("cf-connecting-ip", "")
            try:
                client = str(ipaddress.ip_address(cf)) if cf else c.peer
            except ValueError:
                pass
        try:
            r, w = await self.open_backend("127.0.0.1", port, timeout=5.0)
        except (OSError, asyncio.TimeoutError):
            c.reply("502 Bad Gateway", b"panel is not running\n")
            return
        drop = {b"connection", b"keep-alive", b"proxy-connection", b"upgrade", b"x-real-ip",
                b"x-forwarded-for", b"x-forwarded-proto", b"te"}
        w.write(first if first.endswith(b"\n") else first + b"\r\n")
        for line in raw:
            if line.split(b":", 1)[0].strip().lower() not in drop:
                w.write(line if line.endswith(b"\n") else line + b"\r\n")
        w.write(f"Connection: close\r\nX-Real-IP: {client}\r\nX-Forwarded-Proto: https\r\n\r\n".encode())
        await http_relay(c.reader, c.writer, r, w)

    # ---- listeners & firewall --------------------------------------------------------------
    @staticmethod
    def make_listener(host: str | None, port: int) -> socket.socket:
        """Dual-stack when possible; falls back to plain IPv4."""
        if host is None:
            try:
                s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("::", port))
            except OSError:
                try:
                    s.close()
                except (OSError, UnboundLocalError):
                    pass
                s = None
            if s is not None:
                s.listen(4096)
                s.setblocking(False)
                return s
            host = "0.0.0.0"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(4096)
        s.setblocking(False)
        return s

    @staticmethod
    def open_firewall(ports) -> None:
        """Make sure every listening port is accepted by iptables. Idempotent; also re-applied
        periodically because netfilter-persistent can reload rules after we start."""
        for tool in ("iptables", "ip6tables"):
            for port in ports:
                rule = ["-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]
                try:
                    have = subprocess.run([tool, "-w", "5", "-C", "INPUT", *rule], capture_output=True, timeout=10)
                    if have.returncode == 1:
                        subprocess.run([tool, "-w", "5", "-I", "INPUT", "1", *rule], capture_output=True, timeout=10)
                        log.warning("opened %s port %s", tool, port)
                    elif have.returncode not in (0, 1):
                        break                    # tool missing or unusable: skip it
                except (OSError, subprocess.SubprocessError):
                    break

    async def firewall_loop(self, ports) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, self.open_firewall, ports)
            await asyncio.sleep(FIREWALL_RECHECK)

    async def run(self) -> None:
        http_ports, https_ports = self.cfg.ports()
        ports = sorted({*http_ports, *https_ports})
        listeners = []
        for port in ports:
            try:
                listeners.append((self.make_listener(None, port), f"0.0.0.0:{port}", port))
            except OSError as exc:
                log.error("cannot listen on port %s: %s", port, exc)
        loopback = int(self.cfg.get().get("proxy_port", DEFAULT_PROXY_PORT))
        try:
            listeners.append((self.make_listener("127.0.0.1", loopback), f"127.0.0.1:{loopback}", loopback))
        except OSError as exc:
            log.error("cannot listen on 127.0.0.1:%s: %s", loopback, exc)
        if not listeners:
            log.critical("no listener could be started")
            raise SystemExit(1)
        log.warning("listening on %s (TLS %s)", ", ".join(str(p) for _, _, p in listeners),
                    "ready" if self.tls.get() else "NOT ready: no certificate")
        jobs = [self.accept_loop(s, label) for s, label, _ in listeners]
        if self.firewall:
            jobs.append(self.firewall_loop([p for _, label, p in listeners if not label.startswith("127.")]))
        await asyncio.gather(*jobs)


def setup_logging() -> None:
    handlers: list = []
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3))
    except OSError:
        pass
    handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=getattr(logging, os.environ.get("SSHAUTO_RELAY_LEVEL", "WARNING").upper(), logging.WARNING),
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)


def main() -> None:
    setup_logging()
    if uvloop is None:
        log.critical("uvloop is required (pip3 install uvloop, or apt install python3-uvloop)")
        raise SystemExit(1)
    relay = Relay(Config())
    if hasattr(uvloop, "run"):
        uvloop.run(relay.run())
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        asyncio.run(relay.run())


if __name__ == "__main__":
    main()
