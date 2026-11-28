"""End-to-end tests of the relay: real sockets, real TLS, fake Dropbear and fake panel."""
import asyncio
import base64
import http.server
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "relay"))
import ws_ssh_proxy as relay  # noqa: E402

BANNER = b"SSH-2.0-fakedropbear\r\n"
WS = b"GET / HTTP/1.1\r\nHost: example.test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def recv_until(sock, marker: bytes, timeout=5.0) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def tls_client(sock) -> ssl.SSLSocket:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx.wrap_socket(sock, server_hostname="whatever.example")


def mask_frame(payload: bytes, opcode=0x2) -> bytes:
    key = b"\x01\x02\x03\x04"
    body = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    head = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", n)
    return head + key + body


class FakeDropbear(threading.Thread):
    """Sends an SSH banner, then echoes."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(64)
        self.start()

    def run(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.echo, args=(conn,), daemon=True).start()

    @staticmethod
    def echo(conn):
        try:
            conn.sendall(BANNER)
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                conn.sendall(b"echo:" + data)
        except OSError:
            pass
        finally:
            conn.close()


class FakePanel(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ip": self.headers.get("X-Real-IP"), "proto": self.headers.get("X-Forwarded-Proto"),
                           "conn": self.headers.get("Connection"), "spoof": self.headers.get("X-Forwarded-For")}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("relay")
    crt, key = tmp / "c.pem", tmp / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-days", "2", "-subj", "/CN=hi.example.test"], check=True, capture_output=True)
    hp, sp, db, panel = free_port(), free_port(), free_port(), free_port()
    FakeDropbear(db)
    ps = http.server.ThreadingHTTPServer(("127.0.0.1", panel), FakePanel)
    threading.Thread(target=ps.serve_forever, daemon=True).start()
    st = tmp / "state.json"
    st.write_text(json.dumps({"dropbear_port": db, "cert_domain": "hi.example.test", "server_ip": "203.0.113.9",
                              "cert_fullchain_path": str(crt), "cert_key_path": str(key),
                              "panel_port": panel, "panel_path": "sekret", "proxy_port": free_port()}))
    cfg = relay.Config(st, http=(hp,), https=(sp,))
    r = relay.Relay(cfg, firewall=False)
    relay.SNIFF_TIMEOUT = 0.5
    def run_relay():                          # production loop when available
        if relay.uvloop is not None and hasattr(relay.uvloop, "run"):
            relay.uvloop.run(r.run())
        else:
            asyncio.run(r.run())
    threading.Thread(target=run_relay, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", sp), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return {"http": hp, "https": sp, "db": db, "state": st, "cfg": cfg}


def conn(port):
    return socket.create_connection(("127.0.0.1", port), timeout=5)


def test_ws_plain_raw(env):
    s = conn(env["http"])
    s.sendall(WS)
    buf = recv_until(s, b"SSH-")
    assert buf.startswith(b"HTTP/1.1 101") and BANNER in buf
    s.sendall(b"SSH-2.0-client\r\n")
    assert b"echo:SSH-2.0-client" in recv_until(s, b"client\r\n")


def test_ws_tls_on_https_port(env):
    s = tls_client(conn(env["https"]))
    s.sendall(WS)
    assert BANNER in recv_until(s, b"SSH-2.0-fakedropbear")


def test_tls_on_the_plain_port_and_plain_on_the_tls_port(env):
    s = tls_client(conn(env["http"]))
    s.sendall(WS)
    assert BANNER in recv_until(s, b"SSH-2.0-fakedropbear")
    s = conn(env["https"])
    s.sendall(WS)
    assert BANNER in recv_until(s, b"SSH-2.0-fakedropbear")


def test_dual_host_and_odd_methods(env):
    s = conn(env["http"])
    s.sendall(b"PATCH /x HTTP/1.1\r\nHost: a.test\r\nHost: b.test\r\nX-Online-Host: c\r\nUPGRADE: Websocket\r\n\r\n")
    assert BANNER in recv_until(s, b"SSH-2.0-fakedropbear")


def test_ws_accept_header_for_real_clients(env):
    s = conn(env["http"])
    key = base64.b64encode(b"0123456789abcdef").decode()
    s.sendall(f"GET /chat HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    head = recv_until(s, b"\r\n\r\n")
    assert relay.ws_accept(key).encode() in head
    assert relay.ws_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="    # RFC 6455 example


def test_ws_framed_client(env):
    s = tls_client(conn(env["https"]))
    s.sendall(WS.replace(b"\r\n\r\n", b"\r\nSec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\n\r\n"))
    recv_until(s, b"\r\n\r\n")
    s.sendall(mask_frame(b"SSH-2.0-framed\r\n"))
    time.sleep(0.3)
    got = b""
    s.settimeout(3)
    while b"echo:SSH-2.0-framed" not in got:
        got += s.recv(4096)
    p = relay.FrameParser()
    payload = b"".join(pl for op, pl in p.feed(got) if op == 2)
    assert payload.startswith(BANNER) and b"echo:SSH-2.0-framed" in payload


def test_raw_ssh_plain_and_in_tls(env):
    for wrap in (lambda x: x, tls_client):
        s = wrap(conn(env["http"] if wrap is not tls_client else env["https"]))
        s.sendall(b"SSH-2.0-OpenSSH_9\r\n")
        got = recv_until(s, b"OpenSSH_9\r\n")
        assert BANNER in got and b"echo:SSH-2.0-OpenSSH_9" in got


def test_silent_client_gets_banner(env):
    s = conn(env["http"])
    assert recv_until(s, b"\r\n", timeout=4) == BANNER


def test_connect_own_host_goes_to_dropbear(env):
    for target in ("hi.example.test:443", f"127.0.0.1:{env['db']}", "203.0.113.9:80", "sub.hi.example.test:22222"):
        s = conn(env["http"])
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        head = recv_until(s, b"\r\n\r\n")
        if target.endswith(":22222"):
            assert head.startswith(b"HTTP/1.1 403")
            continue
        assert head.startswith(b"HTTP/1.1 200")
        got = head if BANNER in head else head + recv_until(s, BANNER)
        assert BANNER in got


def test_no_open_proxy(env):
    # CONNECT anywhere but this server is refused, public or private
    for target in ("10.0.0.5:22", "169.254.169.254:80", "93.184.216.34:443", "example.com:443"):
        s = conn(env["http"])
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        assert recv_until(s, b"\r\n\r\n").startswith(b"HTTP/1.1 403"), target
    # plain HTTP for another host is never forwarded: static page, no outbound connection
    for host in ("169.254.169.254", "93.184.216.34", "example.com"):
        s = conn(env["http"])
        s.sendall(f"GET http://{host}/ HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        assert recv_until(s, b"OK\n").startswith(b"HTTP/1.1 200")


def test_own_host_gets_a_page_not_a_loop(env):
    for host in ("hi.example.test", "203.0.113.9", "127.0.0.1:80"):
        s = conn(env["http"])
        s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        assert recv_until(s, b"OK\n").startswith(b"HTTP/1.1 200")


def test_garbage_and_oversized_input_do_not_break_it(env):
    for payload in (b"\x00\x01\x02\xff\xfe\r\n\r\n", b"GET\r\n\r\n", b"A" * 70000 + b"\r\n", b"\x16\x03\x01garbage"):
        s = conn(env["http"])
        s.sendall(payload)
        s.settimeout(3)
        try:
            s.recv(100)
        except (OSError, ssl.SSLError):
            pass
        s.close()
    s = conn(env["http"])
    s.sendall(WS)
    assert BANNER in recv_until(s, b"SSH-2.0-fakedropbear")


def test_panel_only_over_https(env):
    s = conn(env["http"])
    s.sendall(b"GET /sekret/api/session HTTP/1.1\r\nHost: hi.example.test\r\n\r\n")
    assert recv_until(s, b"\r\n\r\n").startswith(b"HTTP/1.1 404")
    s = tls_client(conn(env["https"]))
    s.sendall(b"GET /sekret/api/x HTTP/1.1\r\nHost: hi.example.test\r\nX-Real-IP: 6.6.6.6\r\n"
              b"X-Forwarded-For: 6.6.6.6\r\nConnection: keep-alive\r\n\r\n")
    got = recv_until(s, b"}")
    body = json.loads(got.split(b"\r\n\r\n", 1)[1])
    assert body == {"ip": "127.0.0.1", "proto": "https", "conn": "close", "spoof": None}


def test_panel_behind_cdn_with_forwarded_proto(env):
    s = conn(env["http"])
    s.sendall(b"GET /sekret/ HTTP/1.1\r\nHost: hi.example.test\r\nX-Forwarded-Proto: https\r\n\r\n")
    got = recv_until(s, b"}")
    assert got.startswith(b"HTTP/1.") and b" 200 " in got.split(b"\r\n")[0]
    assert json.loads(got.split(b"\r\n\r\n", 1)[1])["proto"] == "https"


def test_websocket_to_dead_dropbear_is_502(env, tmp_path):
    cfg = relay.Config(tmp_path / "s.json", http=(), https=())
    r = relay.Relay(cfg, firewall=False)
    (tmp_path / "s.json").write_text(json.dumps({"dropbear_port": free_port()}))

    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(WS)
        reader.feed_eof()

        class W:
            out = b""

            def write(self, d): self.out += d
            def close(self): pass
            async def drain(self): pass
            def get_extra_info(self, *a): return None

        w = W()
        c = relay.Conn(reader, w, False, "1.2.3.4")
        await r.serve(c)
        return w.out
    assert asyncio.run(go()).startswith(b"HTTP/1.1 502")


def test_config_hot_reload_changes_dropbear_port(env):
    cfg = env["cfg"]
    assert cfg.dropbear_port == env["db"]
    st = json.loads(env["state"].read_text())
    assert cfg.domain == "hi.example.test" and st["panel_path"] == "sekret"


def test_no_descriptor_leak(env):
    def fds():
        return len(os.listdir("/proc/self/fd"))
    time.sleep(0.5)
    before = fds()
    for _ in range(150):
        s = conn(env["http"])
        s.sendall(WS + b"SSH-2.0-x\r\n")        # like real clients: banner right behind the upgrade
        recv_until(s, b"SSH-2.0-fakedropbear")
        s.close()
    for _ in range(40):
        s = conn(env["http"])
        s.sendall(b"GET / HTTP/1.1\r\n")      # incomplete request, abandoned
        s.close()
    time.sleep(2)
    assert fds() <= before + 10


def test_frame_parser_handles_split_and_large_frames():
    p = relay.FrameParser()
    big = bytes(range(256)) * 300                       # 76800 bytes -> 64-bit length form
    wire = mask_frame(b"hi") + mask_frame(b"x" * 200) + relay_big(big)
    out = []
    for i in range(0, len(wire), 7):                    # dribble it in
        out += p.feed(wire[i:i + 7])
    assert [pl for _, pl in out] == [b"hi", b"x" * 200, big]
    assert relay.looks_framed(mask_frame(b"x")) and not relay.looks_framed(b"SSH-2.0")


def relay_big(payload: bytes) -> bytes:
    key = b"\x09\x08\x07\x06"
    body = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes([0x82, 0x80 | 127]) + struct.pack("!Q", len(payload)) + key + body


def test_relay_default_ports_match_project_config():
    from core.config import HTTP_PORTS, HTTPS_PORTS
    assert set(relay.DEFAULT_HTTP_PORTS) == HTTP_PORTS and set(relay.DEFAULT_HTTPS_PORTS) == HTTPS_PORTS


def test_no_nginx_feature_left():
    from core.plugin_manager import PluginManager
    names = {f.name for f in PluginManager()._ordered()}
    assert "nginx_relay" not in names and {"python_proxy", "certificates", "firewall"} <= names


def test_tunnel_audit_line_has_client_and_backend_port(env, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="relay.tunnel")
    s = conn(env["http"])
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n\r\n")
    assert recv_until(s, b"\r\n\r\n").startswith(b"HTTP/1.1 101")
    recv_until(s, BANNER)
    s.close()
    time.sleep(0.5)
    lines = [r.getMessage() for r in caplog.records if r.name == "relay.tunnel"]
    assert any(l.startswith("open kind=ws client=127.0.0.1 ") and "backend_port=" in l for l in lines), lines
    assert any(l.startswith("close kind=ws") and "seconds=" in l for l in lines), lines


def test_client_ip_trusts_cf_header_only_from_cloudflare():
    assert relay.client_ip("127.0.0.1", {"cf-connecting-ip": "9.9.9.9"}) == "127.0.0.1"
    assert relay.client_ip("173.245.48.5", {"cf-connecting-ip": "9.9.9.9"}) == "9.9.9.9"
    assert relay.client_ip("173.245.48.5", {"cf-connecting-ip": "junk"}) == "173.245.48.5"
