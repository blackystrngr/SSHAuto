"""UFW helpers. sshauto only ever ADDS ufw rules: it never deletes, resets or disables anything."""
from __future__ import annotations

import re

from core.logger import log
from core.shell import Shell

_RULE = re.compile(r"^(\d+)(?:/(tcp|udp))?(?:\s+\(v6\))?\s+ALLOW\b")


def is_active() -> bool:
    res = Shell.run("ufw status", check=False, timeout=15)
    return res.ok and "Status: active" in res.stdout


def allowed_tcp_ports() -> set[int]:
    """TCP ports currently allowed (from `ufw status`)."""
    res = Shell.run("ufw status", check=False, timeout=15)
    ports: set[int] = set()
    for line in res.stdout.splitlines():
        m = _RULE.match(line.strip())
        if m and m.group(2) in (None, "tcp"):
            ports.add(int(m.group(1)))
    return ports


def allow_tcp(port: int) -> bool:
    return Shell.run(f"ufw allow {int(port)}/tcp", check=False, timeout=30).ok


def ensure_ports(ports) -> list[int]:
    """Allow every port that is not allowed yet. Returns the ports that were added."""
    have = allowed_tcp_ports()
    added = []
    for port in sorted(set(ports)):
        if port not in have and allow_tcp(port):
            added.append(port)
    return added


def ensure_enabled(ssh_ports) -> None:
    """Allow SSH first, then enable ufw (never lock the admin out)."""
    for port in sorted(set(ssh_ports)):
        allow_tcp(port)
    if not is_active():
        Shell.run("ufw --force enable", check=False, timeout=30)
        log.info("ufw enabled")
