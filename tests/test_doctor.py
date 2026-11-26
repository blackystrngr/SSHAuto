import socket
import threading

from core.doctor import ws_handshake


def _serve(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(reply)
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_handshake_ok():
    port = _serve(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\nSSH-2.0-dropbear\r\n")
    ok, detail = ws_handshake("127.0.0.1", port)
    assert ok and detail == "SSH-2.0-dropbear"


def test_handshake_502():
    port = _serve(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
    ok, detail = ws_handshake("127.0.0.1", port)
    assert not ok and "502" in detail


def test_handshake_no_banner():
    port = _serve(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
    ok, _ = ws_handshake("127.0.0.1", port)
    assert not ok


def test_handshake_refused():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    ok, _ = ws_handshake("127.0.0.1", port, timeout=1)
    assert not ok
