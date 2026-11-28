"""Plain iptables helpers: the firewall just adds ACCEPT rules for ports.
No ufw, no flush, no policy change, no rule is ever deleted. Rules are inserted at the TOP of INPUT so they
sit in front of Oracle's catch-all REJECT, and saved so they survive a reboot."""
from __future__ import annotations

import re

from core.logger import log
from core.shell import Shell

_DPORT = re.compile(r"-p (tcp|udp)\b.*--dport (\d+)\b.*-j ACCEPT\b")


def available() -> bool:
    return Shell.exists("iptables")


def _rules() -> list[str]:
    res = Shell.run("iptables -w 5 -S INPUT", check=False, timeout=15)
    return [ln for ln in res.stdout.splitlines() if ln.startswith("-A INPUT")] if res.ok else []


def allowed_ports(proto: str = "tcp") -> set[int]:
    """Ports with an ACCEPT rule in INPUT for the given protocol."""
    ports: set[int] = set()
    for line in _rules():
        m = _DPORT.search(line)
        if m and m.group(1) == proto:
            ports.add(int(m.group(2)))
    return ports


def allowed_tcp_ports() -> set[int]:
    return allowed_ports("tcp")


def allow(port: int, proto: str = "tcp") -> bool:
    """Add `-p proto --dport port -j ACCEPT` at the top of INPUT unless it is already there."""
    rule = f"INPUT -p {proto} -m {proto} --dport {int(port)} -j ACCEPT"
    if Shell.run(f"iptables -w 5 -C {rule}", check=False, timeout=15).ok:
        return True
    return Shell.run(f"iptables -w 5 -I {rule}", check=False, timeout=15).ok


def allow_tcp(port: int) -> bool:
    return allow(port, "tcp")


def save() -> None:
    """Persist the rules so they are loaded again at boot."""
    if Shell.exists("netfilter-persistent"):
        Shell.run("netfilter-persistent save", check=False, timeout=60)
    else:
        Shell.run("mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4", check=False, timeout=30)


def ensure_ports(ports, only_if_active: bool = False) -> list[int]:
    """Allow every TCP port that is not allowed yet. Returns the ports that were added.
    (only_if_active is kept for the callers' signature: plain iptables is always 'on'.)"""
    if not available():
        return []
    have = allowed_tcp_ports()
    added = []
    for port in sorted(set(ports)):
        if port not in have and allow_tcp(port):
            added.append(port)
    if added:
        save()
    return added


def allow_dns() -> None:
    for proto in ("tcp", "udp"):
        allow(53, proto)


def tune_conntrack() -> None:
    """OPT-IN (SSHAUTO_TUNE_CONNTRACK=1). Raise the connection-tracking limit."""
    import os
    from pathlib import Path
    if os.environ.get("SSHAUTO_TUNE_CONNTRACK") != "1":
        return
    Shell.run("modprobe nf_conntrack", check=False, timeout=15)
    try:
        Path("/etc/modules-load.d/sshauto-conntrack.conf").write_text("nf_conntrack\n")
        Path("/etc/sysctl.d/99-sshauto-conntrack.conf").write_text(
            "net.netfilter.nf_conntrack_max = 65536\n"
            "net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_close_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_established = 86400\n")
    except OSError as exc:
        log.warning(f"could not tune conntrack: {exc}")
    Shell.run("sysctl --system", check=False, timeout=30)
