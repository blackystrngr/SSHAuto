"""Loopback-only HTTP server for the panel. nginx publishes it under a secret path."""
from __future__ import annotations

import json
import sys
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.config import state
from panel.api import PanelApi, Request

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE = "ssh_panel"
MAX_BODY = 64 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2",
         ".txt": "text/plain; charset=utf-8"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def static_files() -> dict[str, Path]:
    """Whitelist of servable files: '/assets/<relative path>' -> file. Nothing else is ever opened."""
    return {f"/assets/{p.relative_to(STATIC_DIR).as_posix()}": p
            for p in STATIC_DIR.rglob("*") if p.is_file() and p.name != "index.html"}


class Handler(BaseHTTPRequestHandler):
    server_version = "sshauto-panel"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30                       # drop slow / idle connections

    def log_message(self, fmt, *args):  # quiet; failures are reported via log_error / tracebacks
        pass

    do_GET = do_POST = do_DELETE = lambda self: self._handle()

    # ---- plumbing -------------------------------------------------------------------------
    def _client_ip(self) -> str:
        peer = self.client_address[0]
        forwarded = self.headers.get("X-Real-IP", "")
        if peer in ("127.0.0.1", "::1") and forwarded and len(forwarded) < 46 and \
                all(c in "0123456789abcdefABCDEF.:" for c in forwarded):
            return forwarded
        return peer

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, headers: dict | None = None):
        self._send(status, json.dumps(payload).encode(), "application/json", headers)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        if not length:
            return {}
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"{COOKIE}={value}; Path={self.server.base}/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    # ---- routing --------------------------------------------------------------------------
    def _handle(self):
        try:
            base = self.server.base
            parts = urlsplit(self.path)
            if parts.path == base:
                return self._send(301, b"", "text/plain", {"Location": base + "/"})
            if not parts.path.startswith(base + "/"):
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            rel = parts.path[len(base):]
            if rel.startswith("/api/"):
                return self._api(rel[4:], parts.query)
            if self.command != "GET":
                return self._send(405, b"Method not allowed", "text/plain; charset=utf-8")
            return self._static(rel)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc(file=sys.stderr)
            try:
                self._json(500, {"error": "Internal error. Check the panel log."})
            except Exception:
                pass

    def _static(self, rel: str):
        if rel == "/":
            path = STATIC_DIR / "index.html"
        else:
            path = self.server.files.get(rel)
        if not path or not path.exists():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"),
                   cache="no-cache")

    def _api(self, rel: str, query: str):
        if self.command != "GET" and self.headers.get("X-Requested-With") != "sshauto-panel":
            return self._json(403, {"error": "Missing request header."})
        try:
            body = self._read_body() if self.command == "POST" else {}
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Invalid request body."})
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        user = self.server.api.auth.verify(jar[COOKIE].value if COOKIE in jar else None)
        req = Request(method=self.command, path=rel,
                      query={k: v[0] for k, v in parse_qs(query).items()},
                      body=body, ip=self._client_ip(), host=(self.headers.get("Host") or "").split(":")[0],
                      user=user)
        status, payload = self.server.api.dispatch(req, user)
        headers = {}
        if payload.pop("__logout__", False):
            headers["Set-Cookie"] = self._cookie("", 0)
        session_user = payload.pop("__session__", None)
        if session_user:
            headers["Set-Cookie"] = self._cookie(self.server.api.auth.issue(session_user), 12 * 3600)
        self._json(status, payload, headers)


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, base: str, api: PanelApi):
        super().__init__(addr, Handler)
        self.base = "/" + base.strip("/")
        self.api = api
        self.files = static_files()


def serve() -> None:
    data = state.ensure_defaults()
    port, base = data.get("panel_port"), data.get("panel_path")
    if not port or not base:
        raise SystemExit("panel is not configured: run `main.py panel` first")
    api = PanelApi()
    api.sampler.start()
    server = PanelServer(("127.0.0.1", int(port)), base, api)
    print(f"sshauto panel listening on 127.0.0.1:{port}/{base}/", flush=True)
    server.serve_forever()
