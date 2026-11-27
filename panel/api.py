"""JSON API. Every handler takes a Request and returns (status, payload).

All input is validated here; nothing user-supplied ever reaches a shell string.
"""
from __future__ import annotations

import datetime
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from core import blocklists, doctor
from core.config import LOG_DIR, PROXY_PORT_DEFAULT, SQUID_PORT_DEFAULT, state
from core.exceptions import SSHAutoError, ValidationError
from core.shell import Shell
from dashboard.monitor import Monitor
from dashboard.ports import PortManager
from dashboard.users import UserManager
from panel import metrics
from panel.auth import Auth, RateLimiter, generate_password
from panel.jobs import Busy, JobRunner

SERVICES = [
    # unit(s), label, optional, restartable
    (("nginx",), "Web relay", False, True),
    (("ws-ssh-proxy",), "WebSocket proxy", False, True),
    (("dropbear-tunnel",), "Dropbear SSH", False, True),
    (("ssh", "sshd"), "OpenSSH (admin access)", False, True),
    (("fail2ban",), "Brute-force protection", False, True),
    (("badvpn-udpgw",), "UDP gateway", True, True),
    (("sshauto-dnsfilter",), "DNS content filter", True, True),
    (("squid",), "Squid proxy", True, True),
    (("sshauto-panel",), "This panel", False, False),
]
RESTARTABLE = {u for units, _, _, ok in SERVICES if ok for u in units}

LOG_SOURCES = {
    "proxy": ("file", str(LOG_DIR / "proxy.log")),
    "nginx": ("unit", "nginx"),
    "dropbear": ("unit", "dropbear-tunnel"),
    "fail2ban": ("unit", "fail2ban"),
    "dnsfilter": ("unit", "sshauto-dnsfilter"),
    "panel": ("unit", "sshauto-panel"),
}
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str, hint: str | None = None):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    ip: str = "-"
    host: str = ""
    user: str | None = None
    args: tuple = ()


def _int(body: dict, key: str, default=None, lo: int = 0, hi: int = 10 ** 9) -> int:
    raw = body.get(key, default)
    if isinstance(raw, bool) or raw is None:
        raise ApiError(400, f"'{key}' is required")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{key}' must be a number")
    if not lo <= value <= hi:
        raise ApiError(400, f"'{key}' must be between {lo} and {hi}")
    return value


def _str(body: dict, key: str, required: bool = True, maxlen: int = 200) -> str:
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise ApiError(400, f"'{key}' is required")
        return ""
    if not isinstance(value, str) or len(value) > maxlen:
        raise ApiError(400, f"'{key}' is invalid")
    return value


class PanelApi:
    def __init__(self, sampler=None, jobs=None, auth=None, limiter=None):
        self.sampler = sampler or metrics.Sampler()
        self.jobs = jobs or JobRunner()
        self.auth = auth or Auth()
        self.limiter = limiter or RateLimiter()
        self._cache: dict[str, tuple[float, object]] = {}
        self.routes = [
            ("GET", r"/session", self.session, False),
            ("POST", r"/login", self.login, False),
            ("POST", r"/logout", self.logout, True),
            ("GET", r"/overview", self.overview, True),
            ("GET", r"/metrics", self.metrics, True),
            ("GET", r"/connection", self.connection, True),
            ("GET", r"/users", self.users_list, True),
            ("POST", r"/users", self.users_create, True),
            ("DELETE", r"/users/([a-z_][a-z0-9_-]{2,31})", self.users_delete, True),
            ("POST", r"/users/([a-z_][a-z0-9_-]{2,31})/lock", self.users_lock, True),
            ("POST", r"/users/([a-z_][a-z0-9_-]{2,31})/unlock", self.users_unlock, True),
            ("POST", r"/users/([a-z_][a-z0-9_-]{2,31})/expiry", self.users_expiry, True),
            ("GET", r"/ports", self.ports_list, True),
            ("POST", r"/ports", self.ports_add, True),
            ("DELETE", r"/ports/(http|https)/(\d{1,5})", self.ports_remove, True),
            ("GET", r"/services", self.services, True),
            ("POST", r"/services/([a-z0-9-]{1,40})/restart", self.service_restart, True),
            ("GET", r"/filter", self.filter_get, True),
            ("POST", r"/filter", self.filter_set, True),
            ("POST", r"/filter/update", self.filter_update, True),
            ("DELETE", r"/filter", self.filter_remove, True),
            ("GET", r"/squid", self.squid_get, True),
            ("POST", r"/squid", self.squid_set, True),
            ("DELETE", r"/squid", self.squid_remove, True),
            ("GET", r"/doctor", self.doctor_get, True),
            ("POST", r"/doctor/repair", self.doctor_repair, True),
            ("POST", r"/speedtest", self.speedtest, True),
            ("GET", r"/jobs/([0-9a-f]{10})", self.job_get, True),
            ("GET", r"/logs", self.logs, True),
            ("POST", r"/settings/password", self.change_password, True),
        ]
        self._compiled = [(m, re.compile(f"^{p}$"), h, a) for m, p, h, a in self.routes]

    # ---- dispatch -------------------------------------------------------------------------
    def dispatch(self, req: Request, token_user: str | None) -> tuple[int, dict]:
        matched_path = False
        for method, pattern, handler, needs_auth in self._compiled:
            m = pattern.match(req.path)
            if not m:
                continue
            matched_path = True
            if method != req.method:
                continue
            if needs_auth and not token_user:
                return 401, {"error": "Sign in to continue."}
            req.user, req.args = token_user, m.groups()
            try:
                return handler(req)
            except ApiError as exc:
                return exc.status, {"error": exc.message, "hint": exc.hint}
            except SSHAutoError as exc:
                return 400, {"error": exc.message, "hint": exc.hint}
        return (405, {"error": "Method not allowed."}) if matched_path else (404, {"error": "Not found."})

    def _cached(self, key: str, ttl: float, fn):
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def _invalidate(self, *keys: str) -> None:
        for k in keys:
            self._cache.pop(k, None)

    # ---- session --------------------------------------------------------------------------
    def session(self, req):
        return 200, {"configured": self.auth.configured(), "authenticated": bool(req.user), "user": req.user}

    def login(self, req):
        wait = self.limiter.blocked(req.ip)
        if wait:
            raise ApiError(429, f"Too many failed sign-ins. Try again in {wait // 60 + 1} min.")
        user, password = _str(req.body, "username", maxlen=64), _str(req.body, "password", maxlen=256)
        if not self.auth.check(user, password):
            self.limiter.fail(req.ip)
            time.sleep(0.4)
            raise ApiError(401, "Wrong username or password.")
        self.limiter.success(req.ip)
        return 200, {"ok": True, "user": user, "__session__": user}

    def logout(self, req):
        return 200, {"ok": True, "__logout__": True}

    def change_password(self, req):
        current, new = _str(req.body, "current", maxlen=256), _str(req.body, "new", maxlen=128)
        if len(new) < 10:
            raise ApiError(400, "The new password needs at least 10 characters.")
        if not self.auth.check(req.user, current):
            raise ApiError(403, "Current password is wrong.")
        self.auth.set_credentials(req.user, new)
        return 200, {"ok": True, "__session__": req.user}

    # ---- overview / metrics ---------------------------------------------------------------
    def overview(self, req):
        data = state.ensure_defaults()
        return 200, {
            "system": metrics.system_info(),
            "now": self.sampler.latest(),
            "services": self._services(),
            "users": len(self._users()),
            "domain": data.get("cert_domain") or req.host,
            "filter": self._filter_summary(),
            "ports": {"proxy": data.get("proxy_port", PROXY_PORT_DEFAULT),
                      "dropbear": data.get("dropbear_port"), "ssh": data.get("ssh_port")},
        }

    def metrics(self, req):
        return 200, {"series": self.sampler.series(), "interval": metrics.SAMPLE_SECONDS}

    def connection(self, req):
        data = state.ensure_defaults()
        ports = PortManager().list_all()
        domain = data.get("cert_domain") or req.host
        return 200, {
            "domain": domain, "http_ports": ports["http"], "https_ports": ports["https"],
            "ssh_port": data.get("ssh_port"), "dropbear_port": data.get("dropbear_port"),
            "payload": f"GET / HTTP/1.1[crlf]Host: {domain}[crlf]Upgrade: websocket[crlf][crlf]",
        }

    # ---- users ----------------------------------------------------------------------------
    def _users(self):
        def load():
            out = []
            for u in UserManager().list():
                days = None
                try:
                    exp = datetime.datetime.strptime(u.expires, "%b %d, %Y").date()
                    days = (exp - datetime.date.today()).days
                except ValueError:
                    pass
                out.append({"username": u.username, "expires": u.expires, "locked": u.locked,
                            "days_left": days, "expired": days is not None and days < 0})
            return out
        return self._cached("users", 10, load)

    def users_list(self, req):
        return 200, {"users": self._users()}

    def users_create(self, req):
        username = _str(req.body, "username", maxlen=32)
        password = _str(req.body, "password", required=False, maxlen=64) or generate_password(12)
        days = _int(req.body, "days", 30, 0, 3650)
        UserManager().create(username, password, days)
        self._invalidate("users")
        _, conn = self.connection(req)
        return 201, {"username": username, "password": password, "days": days, "connection": conn}

    def users_delete(self, req):
        UserManager().delete(req.args[0])
        self._invalidate("users")
        return 200, {"ok": True}

    def users_lock(self, req):
        UserManager().lock(req.args[0])
        self._invalidate("users")
        return 200, {"ok": True}

    def users_unlock(self, req):
        UserManager().unlock(req.args[0])
        self._invalidate("users")
        return 200, {"ok": True}

    def users_expiry(self, req):
        UserManager().set_expiry(req.args[0], _int(req.body, "days", None, 0, 3650))
        self._invalidate("users")
        return 200, {"ok": True}

    # ---- ports ----------------------------------------------------------------------------
    def _reserved_ports(self) -> set[int]:
        d = state.ensure_defaults()
        found = {22, d.get("ssh_port"), d.get("dropbear_port"), d.get("proxy_port", PROXY_PORT_DEFAULT),
                 d.get("panel_port"), d.get("squid_port"), 7300, 53}
        return {p for p in found if p}

    def _ports(self) -> dict:
        d = state.ensure_defaults()
        return dict(PortManager().list_all(), custom_http=d.get("custom_http_ports", []),
                    custom_https=d.get("custom_https_ports", []))

    def ports_list(self, req):
        return 200, self._ports()

    def ports_add(self, req):
        kind = _str(req.body, "kind", maxlen=5)
        port = _int(req.body, "port", None, 1, 65535)
        if port in self._reserved_ports():
            raise ApiError(400, f"Port {port} is used by another sshauto service.")
        PortManager().add(port, kind)
        return 201, self._ports()

    def ports_remove(self, req):
        PortManager().remove(int(req.args[1]), req.args[0])
        return 200, self._ports()

    # ---- services -------------------------------------------------------------------------
    def _services(self):
        def load():
            units = [f"{u}.service" for names, *_ in SERVICES for u in names]
            res = Shell.run(["systemctl", "show", "-p", "Id,LoadState,ActiveState", *units],
                            check=False, timeout=10)
            info = {}
            for block in res.stdout.split("\n\n"):
                kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                if "Id" in kv:
                    info[kv["Id"].removesuffix(".service")] = kv
            out = []
            for names, label, optional, restartable in SERVICES:
                kv = next((info[n] for n in names if info.get(n, {}).get("LoadState") == "loaded"), None)
                unit = next((n for n in names if info.get(n, {}).get("LoadState") == "loaded"), names[0])
                status = "missing" if kv is None else kv.get("ActiveState", "unknown")
                out.append({"unit": unit, "label": label, "state": status,
                            "optional": optional, "restartable": restartable})
            return out
        return self._cached("services", 4, load)

    def services(self, req):
        return 200, {"services": self._services()}

    def service_restart(self, req):
        unit = req.args[0]
        if unit not in RESTARTABLE:
            raise ApiError(400, "That service cannot be restarted from here.")
        res = Shell.run(["systemctl", "restart", unit], check=False, timeout=60)
        self._invalidate("services")
        if not res.ok:
            raise ApiError(500, f"Could not restart {unit}.", hint=res.stderr.strip()[-200:] or None)
        return 200, {"ok": True, "services": self._services()}

    # ---- content filter -------------------------------------------------------------------
    def _filter_summary(self) -> dict:
        from features.content_filter import UNIT_PATH
        d = state.ensure_defaults()
        installed = UNIT_PATH.exists()
        counts = {c: blocklists.count_entries(blocklists.LISTS_DIR / f"{c}.conf") for c in blocklists.CATEGORIES}
        return {"installed": installed,
                "categories": [c for c, n in counts.items() if n],
                "counts": counts, "private": bool(d.get("filter_private")),
                "allow": d.get("filter_allow", []),
                "descriptions": blocklists.DESCRIPTIONS}

    def filter_get(self, req):
        return 200, self._filter_summary()

    def _job(self, title: str, fn):
        try:
            return 202, {"job": self.jobs.start(title, fn)}
        except Busy as exc:
            raise ApiError(409, str(exc))

    def filter_set(self, req):
        cats = req.body.get("categories", [])
        allow = req.body.get("allow", [])
        if not isinstance(cats, list) or not isinstance(allow, list) or len(allow) > 200:
            raise ApiError(400, "Invalid filter settings.")
        bad = [c for c in cats if c not in blocklists.CATEGORIES]
        if bad:
            raise ApiError(400, f"Unknown categories: {', '.join(map(str, bad))}")
        allow = [str(a).strip().lower() for a in allow if str(a).strip()]
        if any(not DOMAIN_RE.match(a) for a in allow):
            raise ApiError(400, "Allowlist entries must be domain names such as example.com.")
        data = state.ensure_defaults()
        data.update(filter_categories=list(dict.fromkeys(cats)), filter_allow=allow,
                    filter_private=bool(req.body.get("block_private")))
        state.save(data)

        def run():
            from features.content_filter import ContentFilterFeature
            if not ContentFilterFeature().safe_install():
                raise RuntimeError("Filter install failed")
        return self._job("Apply content filter", run)

    def filter_update(self, req):
        def run():
            from features.content_filter import ContentFilterFeature
            ContentFilterFeature().update()
        return self._job("Update blocklists", run)

    def filter_remove(self, req):
        def run():
            from features.content_filter import ContentFilterFeature
            ContentFilterFeature().remove()
        return self._job("Remove content filter", run)

    # ---- squid ----------------------------------------------------------------------------
    def squid_get(self, req):
        from features.squid_proxy import SquidProxyFeature
        d = state.ensure_defaults()
        return 200, {"installed": SquidProxyFeature().is_installed(), "port": d.get("squid_port"),
                     "username": d.get("squid_user"), "password": d.get("squid_pass")}

    def squid_set(self, req):
        from features.squid_proxy import SquidProxyFeature, _validate
        d = state.ensure_defaults()
        user = _str(req.body, "username", required=False, maxlen=32) or d["squid_user"]
        password = _str(req.body, "password", required=False, maxlen=64) or d["squid_pass"]
        port = _int(req.body, "port", d.get("squid_port") or SQUID_PORT_DEFAULT, 1, 65535)
        try:
            _validate(user, password, port)
        except ValidationError as exc:
            raise ApiError(400, exc.message)
        if port in self._reserved_ports() - {d.get("squid_port")}:
            raise ApiError(400, f"Port {port} is used by another sshauto service.")
        d.update(squid_user=user, squid_pass=password, squid_port=port)
        state.save(d)

        def run():
            if not SquidProxyFeature().safe_install():
                raise RuntimeError("Squid install failed")
        return self._job("Apply Squid proxy", run)

    def squid_remove(self, req):
        def run():
            from features.squid_proxy import SquidProxyFeature
            SquidProxyFeature().remove()
        return self._job("Remove Squid proxy", run)

    # ---- doctor / jobs / tools ------------------------------------------------------------
    def doctor_get(self, req):
        checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in doctor.run_checks()]
        return 200, {"checks": checks, "ok": all(c["ok"] for c in checks), "at": int(time.time())}

    def doctor_repair(self, req):
        def run():
            if not doctor.run(heal=True):
                raise RuntimeError("Some checks still fail")
        return self._job("Check and repair", run)

    def speedtest(self, req):
        def run():
            mbps = Monitor().speed_test()
            if mbps is None:
                raise RuntimeError("Speed test failed (no outbound access?)")
            return {"mbps": mbps}
        return self._job("Speed test", run)

    def job_get(self, req):
        job = self.jobs.get(req.args[0])
        if not job:
            raise ApiError(404, "Unknown job.")
        return 200, job

    # ---- logs -----------------------------------------------------------------------------
    def logs(self, req):
        source = req.query.get("source", "proxy")
        if source not in LOG_SOURCES:
            raise ApiError(400, "Unknown log source.")
        try:
            lines = max(20, min(500, int(req.query.get("lines", 200))))
        except ValueError:
            lines = 200
        kind, target = LOG_SOURCES[source]
        if kind == "file":
            path = Path(target)
            text = ""
            if path.exists():
                with path.open(errors="replace") as fh:
                    text = "".join(deque(fh, maxlen=lines))
        else:
            res = Shell.run(["journalctl", "-u", target, "-n", str(lines), "--no-pager", "-o", "short-iso"],
                            check=False, timeout=15)
            text = res.stdout
        return 200, {"source": source, "lines": text.splitlines()[-lines:], "sources": list(LOG_SOURCES)}


def new_secret_path() -> str:
    return secrets.token_urlsafe(9).replace("_", "x").replace("-", "y")


__all__ = ["PanelApi", "Request", "ApiError", "ValidationError", "new_secret_path"]
