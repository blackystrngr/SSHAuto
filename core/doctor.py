"""End-to-end self check (and repair) of the tunnel path.

    client ─▶ relay (public ports, TLS or plain) ─▶ dropbear 127.0.0.1:<dropbear_port>
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from core import blocklists
from core.config import DROPBEAR_PORT_DEFAULT, HTTP_PORTS, HTTPS_PORTS, SYSTEMD_DIR, state
from core.logger import log
from core.shell import Shell

WS_REQUEST = (b"GET / HTTP/1.1\r\nHost: localhost\r\n"
              b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str | None = None   # feature to reinstall when this check fails


def ws_handshake(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Send a WebSocket upgrade and expect `101` followed by the SSH banner."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(WS_REQUEST)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf.startswith(b"HTTP/1.1 101"):
                return False, f"expected 101, got {buf[:30]!r}"
            rest = buf.split(b"\r\n\r\n", 1)[1]
            while b"\n" not in rest:
                chunk = sock.recv(256)
                if not chunk:
                    break
                rest += chunk
            if rest.startswith(b"SSH-"):
                return True, rest.split(b"\r\n")[0].decode(errors="replace")
            return False, "101 ok but no SSH banner from dropbear"
    except OSError as exc:
        return False, str(exc)


def ws_handshake_tls(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Same as ws_handshake but over TLS, for the direct HTTPS relay ports."""
    import ssl as _ssl
    try:
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw) as sock:
                sock.settimeout(timeout)
                sock.sendall(WS_REQUEST)
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if not buf.startswith(b"HTTP/1.1 101"):
                    return False, f"expected 101, got {buf[:30]!r}"
                rest = buf.split(b"\r\n\r\n", 1)[1]
                while b"\n" not in rest:
                    chunk = sock.recv(256)
                    if not chunk:
                        break
                    rest += chunk
                if rest.startswith(b"SSH-"):
                    return True, rest.split(b"\r\n")[0].decode(errors="replace")
                return False, "101 ok but no SSH banner from dropbear"
    except OSError as exc:
        return False, str(exc)


def _publicly_listening(port: int) -> bool:
    out = Shell.run("ss -ltn", check=False, timeout=10).stdout
    for line in out.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 3 and fields[3].endswith(f":{port}") and not fields[3].startswith("127."):
            return True
    return False


def _closed_ports(ports: list[int]) -> list[int]:
    """Relay ports without an iptables ACCEPT rule (none if iptables is missing)."""
    from core import iptables
    if not iptables.available():
        return []
    have = iptables.allowed_tcp_ports()
    return [p for p in ports if p not in have]


def https_get(host: str, port: int, path: str, timeout: float = 5.0) -> tuple[bool, str]:
    """GET over TLS (certificate not verified: this talks to ourselves)."""
    import ssl as _ssl
    try:
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw) as sock:
            sock.settimeout(timeout)
            sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            buf = b""
            while b"\r\n" not in buf:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\r\n")[0].decode(errors="replace")
            return " 200 " in line, line
    except OSError as exc:
        return False, str(exc)


def run_checks() -> list[Check]:
    data = state.ensure_defaults()
    dropbear_port = int(data.get("dropbear_port", DROPBEAR_PORT_DEFAULT))
    checks: list[Check] = []

    for unit, feature in (("dropbear-tunnel", "dropbear_service"), ("ws-ssh-proxy", "python_proxy")):
        ok = Shell.run(f"systemctl is-active {unit}", check=False, timeout=10).ok
        checks.append(Check(f"service {unit}", ok, "" if ok else "not active", feature))

    exposed = _publicly_listening(dropbear_port)
    checks.append(Check(f"dropbear :{dropbear_port} loopback only", not exposed,
                        "listening on a public address" if exposed else "", "dropbear_service"))

    http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
    https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))
    for p in http_ports:
        ok, detail = ws_handshake("127.0.0.1", p)
        checks.append(Check(f"relay :{p} WebSocket → dropbear", ok, detail, "python_proxy"))
    if data.get("cert_fullchain_path"):
        for p in https_ports:
            ok, detail = ws_handshake_tls("127.0.0.1", p)
            checks.append(Check(f"relay :{p} TLS WebSocket → dropbear", ok, detail, "python_proxy"))
        if http_ports:                                    # protocol sniffing: TLS on a "plain" port
            ok, detail = ws_handshake_tls("127.0.0.1", http_ports[0])
            checks.append(Check(f"relay :{http_ports[0]} accepts TLS too", ok, detail, "python_proxy"))
    else:
        checks.append(Check("TLS relay ports", False, "no certificate issued yet", "certificates"))

    closed = _closed_ports(http_ports + https_ports)
    checks.append(Check("firewall allows the relay ports", not closed,
                        f"blocked: {', '.join(map(str, closed))}" if closed else "", "firewall"))

    checks.extend(_filter_checks())
    checks.extend(_panel_checks(data))
    return checks


def _panel_checks(data: dict) -> list[Check]:
    """Only when the optional web panel is installed."""
    if not (SYSTEMD_DIR / "sshauto-panel.service").exists():
        return []
    from features.web_panel import local_ok
    active = Shell.run("systemctl is-active sshauto-panel", check=False, timeout=10).ok
    checks = [Check("service sshauto-panel", active, "" if active else "not active", "web_panel")]
    ok = bool(data.get("panel_port") and data.get("panel_path")) and \
        local_ok(int(data["panel_port"]), data["panel_path"])
    checks.append(Check("panel answers", ok, "" if ok else "no HTTP 200 on loopback", "web_panel"))
    if ok:
        ok, detail = https_get("127.0.0.1", 443, f"/{data['panel_path']}/")
        checks.append(Check("panel reachable through the relay (:443)", ok, "" if ok else detail, "python_proxy"))
    return checks


def _filter_checks() -> list[Check]:
    """Only when the optional content filter is installed."""
    if not (SYSTEMD_DIR / f"{blocklists.DNSFILTER_UNIT}.service").exists():
        return []
    fix = "content_filter"
    unit = blocklists.DNSFILTER_UNIT
    active = Shell.run(f"systemctl is-active {unit}", check=False, timeout=10).ok
    checks = [Check(f"service {unit}", active, "" if active else "not active", fix)]
    rc = blocklists.dns_rcode(blocklists.FILTER_ADDR, "cloudflare.com")
    checks.append(Check("dns filter answers", rc is not None, "" if rc is not None else "no answer", fix))
    sample = blocklists.sample_blocked_domain()
    if sample:
        rc = blocklists.dns_rcode(blocklists.FILTER_ADDR, sample)
        checks.append(Check("dns filter blocks", rc == 3, "" if rc == 3 else f"{sample} rcode={rc}", fix))
    for svc in ("dropbear-tunnel", "ws-ssh-proxy"):
        shown = Shell.run(f"systemctl show -p BindReadOnlyPaths --value {svc}", check=False, timeout=10)
        used = str(blocklists.RESOLV_CONF) in shown.stdout
        checks.append(Check(f"{svc} uses filtered DNS", used, "" if used else "bind-mount missing", fix))
    return checks


def _report(checks: list[Check]) -> list[Check]:
    for c in checks:
        (log.success if c.ok else log.error)(f"{c.name:<38} {c.detail}")
    return [c for c in checks if not c.ok]


def run(heal: bool = True, rounds: int = 2) -> bool:
    """Run all checks; on failure reinstall the affected features and re-check."""
    from core.plugin_manager import PluginManager

    log.rule("doctor")
    for attempt in range(rounds + 1):
        failed = _report(run_checks())
        if not failed:
            log.success("Tunnel path verified end-to-end.")
            return True
        if not heal or attempt == rounds:
            log.error("Tunnel is NOT working. See errors above; logs: /var/log/sshauto/")
            return False
        wanted = {c.fix for c in failed if c.fix}
        log.warning(f"repairing: {', '.join(sorted(wanted))}")
        manager = PluginManager()
        for feature in manager._ordered(sorted(wanted)):
            if feature.name in wanted:
                feature.safe_install()
        time.sleep(2)
    return False
