"""Panel: auth primitives, HTTP surface (real server on an ephemeral port), metrics parsers."""
import http.client
import json
import threading

import pytest

import panel.api as papi
import panel.auth as pauth
import panel.server as srv
from core.config import StateStore
from panel import metrics
from panel.api import PanelApi

PW = "correct-horse-battery"


# ---- auth ---------------------------------------------------------------------------------
def test_hash_roundtrip():
    h = pauth.hash_password(PW)
    assert pauth.verify_password(PW, h)
    assert not pauth.verify_password("nope", h)
    assert not pauth.verify_password(PW, "garbage")
    assert h != pauth.hash_password(PW)          # salted


def test_rate_limiter():
    t = [0.0]
    rl = pauth.RateLimiter(clock=lambda: t[0])
    for _ in range(pauth.MAX_FAILURES):
        assert rl.blocked("1.1.1.1") == 0
        rl.fail("1.1.1.1")
    assert rl.blocked("1.1.1.1") > 0
    assert rl.blocked("2.2.2.2") == 0
    t[0] += pauth.WINDOW + 1
    assert rl.blocked("1.1.1.1") == 0


def test_global_limit():
    t = [0.0]
    rl = pauth.RateLimiter(clock=lambda: t[0])
    for i in range(pauth.GLOBAL_MAX_FAILURES):
        rl.fail(f"10.0.0.{i}")
    assert rl.blocked("9.9.9.9") > 0


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


def test_session_token(store):
    a = pauth.Auth(store)
    assert not a.configured()
    a.set_credentials("admin", PW)
    assert a.check("admin", PW) and not a.check("admin", "x") and not a.check("root", PW)
    tok = a.issue("admin")
    assert a.verify(tok) == "admin"
    assert a.verify(tok + "x") is None
    assert a.verify(None) is None
    assert a.verify(tok, now=__import__("time").time() + pauth.SESSION_TTL + 5) is None
    a.set_credentials("admin", "another-password")     # rotates secret
    assert a.verify(tok) is None


# ---- http ---------------------------------------------------------------------------------
@pytest.fixture
def server(store, monkeypatch):
    monkeypatch.setattr(papi, "state", store)
    auth = pauth.Auth(store)
    auth.set_credentials("admin", PW)
    api = PanelApi(sampler=metrics.Sampler(), auth=auth)
    s = srv.PanelServer(("127.0.0.1", 0), "sec", api)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def call(server, method, path, body=None, cookie=None, csrf=True):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    headers = {}
    if csrf:
        headers["X-Requested-With"] = "sshauto-panel"
    if cookie:
        headers["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = "application/json"
    c.request(method, path, data, headers)
    r = c.getresponse()
    raw = r.read()
    return r, raw


def login(server):
    r, _ = call(server, "POST", "/sec/api/login", {"username": "admin", "password": PW})
    assert r.status == 200
    return r.getheader("Set-Cookie").split(";")[0]


def test_wrong_base_is_404(server):
    assert call(server, "GET", "/")[0].status == 404
    assert call(server, "GET", "/other/")[0].status == 404


def test_index_and_security_headers(server):
    r, body = call(server, "GET", "/sec/")
    assert r.status == 200 and b"<div id=\"root\">" in body
    assert "default-src 'none'" in r.getheader("Content-Security-Policy") or "default-src" in r.getheader("Content-Security-Policy")
    assert r.getheader("X-Content-Type-Options") == "nosniff"


def test_static_whitelist_and_traversal(server):
    assert call(server, "GET", "/sec/assets/app.js")[0].status == 200
    assert call(server, "GET", "/sec/assets/../../core/config.py")[0].status == 404
    assert call(server, "GET", "/sec/assets/%2e%2e/%2e%2e/main.py")[0].status == 404
    assert call(server, "GET", "/sec/assets/nothing.js")[0].status == 404


def test_api_requires_session_and_csrf_header(server):
    assert call(server, "GET", "/sec/api/overview")[0].status == 401
    ck = login(server)
    assert call(server, "POST", "/sec/api/logout", {}, cookie=ck, csrf=False)[0].status == 403
    r, body = call(server, "GET", "/sec/api/session", cookie=ck)
    assert json.loads(body)["authenticated"] is True


def test_login_failure_and_cookie_flags(server):
    r, _ = call(server, "POST", "/sec/api/login", {"username": "admin", "password": "bad"})
    assert r.status == 401
    r, _ = call(server, "POST", "/sec/api/login", {"username": "admin", "password": PW})
    ck = r.getheader("Set-Cookie")
    assert "HttpOnly" in ck and "SameSite=Strict" in ck


def test_login_lockout(server):
    for _ in range(pauth.MAX_FAILURES):
        call(server, "POST", "/sec/api/login", {"username": "admin", "password": "bad"})
    r, _ = call(server, "POST", "/sec/api/login", {"username": "admin", "password": PW})
    assert r.status == 429


def test_logout_clears_cookie(server):
    ck = login(server)
    r, _ = call(server, "POST", "/sec/api/logout", {}, cookie=ck)
    assert r.status == 200 and "Max-Age=0" in r.getheader("Set-Cookie")


def test_invalid_json_is_400(server):
    ck = login(server)
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    c.request("POST", "/sec/api/users", "{not json", {"X-Requested-With": "sshauto-panel", "Cookie": ck,
                                                    "Content-Type": "application/json"})
    assert c.getresponse().status == 400


def test_user_create_validation(server, monkeypatch):
    ck = login(server)
    r, body = call(server, "POST", "/sec/api/users", {"username": "Bad Name!", "password": "x", "days": 5}, cookie=ck)
    assert r.status == 400


def test_reserved_port_refused(server):
    ck = login(server)
    r, _ = call(server, "POST", "/sec/api/ports", {"kind": "https", "port": 22}, cookie=ck)
    assert r.status == 400


def wait_job(server, ck, job_id, timeout=5):
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        r, body = call(server, "GET", f"/sec/api/jobs/{job_id}", cookie=ck)
        job = json.loads(body)
        if job["status"] != "running":
            return job
        _time.sleep(0.05)
    raise TimeoutError("job did not finish")


def test_fail2ban_toggle(server, monkeypatch):
    import panel.api as papi_mod

    installed = {"v": False}

    class FakeFail2ban:
        def is_installed(self):
            return installed["v"]

        def safe_install(self):
            installed["v"] = True
            return True

        def remove(self):
            installed["v"] = False

    import features.fail2ban_service as f2b_mod
    monkeypatch.setattr(f2b_mod, "Fail2banServiceFeature", FakeFail2ban)
    monkeypatch.setattr(papi_mod.Shell, "run", lambda *a, **k: type("R", (), {"ok": True})())

    ck = login(server)
    r, body = call(server, "GET", "/sec/api/fail2ban", cookie=ck)
    assert r.status == 200 and json.loads(body)["installed"] is False

    r, body = call(server, "POST", "/sec/api/fail2ban", {}, cookie=ck)
    assert r.status == 202
    job = wait_job(server, ck, json.loads(body)["job"])
    assert job["status"] == "done"
    r, body = call(server, "GET", "/sec/api/fail2ban", cookie=ck)
    assert json.loads(body)["installed"] is True

    r, body = call(server, "DELETE", "/sec/api/fail2ban", cookie=ck)
    job = wait_job(server, ck, json.loads(body)["job"])
    assert job["status"] == "done"
    r, body = call(server, "GET", "/sec/api/fail2ban", cookie=ck)
    assert json.loads(body)["installed"] is False


# ---- metrics ------------------------------------------------------------------------------
def test_parse_meminfo():
    used, total = metrics.parse_meminfo("MemTotal:  1000 kB\nMemAvailable:  250 kB\n")
    assert total == 1000 * 1024 and used == 750 * 1024


def test_parse_cpu_and_netdev():
    assert metrics.parse_cpu("cpu  10 0 10 80 0 0 0 0 0 0\n") == (20, 100)
    text = "Inter-|\n face |\n  eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n"
    assert metrics.parse_netdev(text, "eth0") == (100, 200)
    assert metrics.parse_netdev(text, "wlan9") is None
