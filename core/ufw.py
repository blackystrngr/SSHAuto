"""UFW helpers. The firewall is managed through ufw: no flush, no inserted rules, no ufw rule is ever
deleted. One exception: release_oracle_reject() removes Oracle's single catch-all INPUT REJECT."""
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


def ensure_ports(ports, only_if_active: bool = False) -> list[int]:
    """Allow every port that is not allowed yet. Returns the ports that were added.
    only_if_active=True: do nothing unless the firewall feature has been turned on (ufw active)."""
    if only_if_active and not is_active():
        return []
    have = allowed_tcp_ports()
    added = []
    for port in sorted(set(ports)):
        if port not in have and allow_tcp(port):
            added.append(port)
    return added


def _shadowed() -> bool:
    """READ-ONLY: is a REJECT/DROP rule in INPUT ahead of ufw's chains?"""
    res = Shell.run("iptables -w 5 -S INPUT", check=False, timeout=15)
    rules = [l for l in res.stdout.splitlines() if l.startswith("-A INPUT")]
    hook = next((i for i, l in enumerate(rules)
                 if "ufw-before-logging-input" in l or "ufw-user-input" in l), None)
    block = next((i for i, l in enumerate(rules) if "-j REJECT" in l or l.endswith("-j DROP")), None)
    return hook is not None and block is not None and block < hook


OCI_REJECT = "INPUT -j REJECT --reject-with icmp-host-prohibited"


def release_oracle_reject() -> None:
    """Oracle Cloud images ship `INPUT ... -j REJECT` (last rule) in /etc/iptables/rules.v4, loaded
    at every boot by netfilter-persistent. ufw appends its chains AFTER it, so ports opened with
    `ufw allow` would stay blocked. The ONLY iptables change sshauto makes, and only when that
    shadowing is detected: delete that single REJECT rule and stop netfilter-persistent from
    re-loading the saved copy at boot (package purge). All other Oracle rules stay as they are.
    Opt out with SSHAUTO_KEEP_OCI_REJECT=1."""
    import os
    if not _shadowed():
        return
    if os.environ.get("SSHAUTO_KEEP_OCI_REJECT") == "1":
        log.warning("Oracle's INPUT REJECT shadows ufw (ports stay blocked); SSHAUTO_KEEP_OCI_REJECT=1 set, leaving it")
        return
    if Shell.run(f"iptables -w 5 -C {OCI_REJECT}", check=False, timeout=15).ok:
        Shell.run(f"iptables -w 5 -D {OCI_REJECT}", check=False, timeout=15)
        log.warning("removed Oracle's catch-all INPUT REJECT (it sat in front of ufw's rules)")
    Shell.run("DEBIAN_FRONTEND=noninteractive apt-get purge -y netfilter-persistent iptables-persistent",
              check=False, timeout=180)
    log.info("netfilter-persistent purged: ufw alone loads the firewall at boot")


def tune_conntrack() -> None:
    """OPT-IN (SSHAUTO_TUNE_CONNTRACK=1). Raise the connection-tracking limit. The default (~7680 on a 1 GB VPS) fills up from tunnel
    traffic and scanners; at the limit the kernel silently drops new connections (random 1s/2s/7s
    connect delays and timeouts)."""
    import os
    from pathlib import Path
    if os.environ.get("SSHAUTO_TUNE_CONNTRACK") != "1":
        return          # not an Ubuntu default: off unless asked for
    Shell.run("modprobe nf_conntrack", check=False, timeout=15)
    try:
        Path("/etc/modules-load.d/sshauto-conntrack.conf").write_text("nf_conntrack\n")
        Path("/etc/sysctl.d/99-sshauto-conntrack.conf").write_text(
            "net.netfilter.nf_conntrack_max = 65536\n"
            "net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_close_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 30\n"
            "net.netfilter.nf_conntrack_tcp_timeout_established = 86400\n")
        hs = Path("/sys/module/nf_conntrack/parameters/hashsize")
        if hs.exists():
            hs.write_text("16384")
    except OSError as exc:
        log.warning(f"could not tune conntrack: {exc}")
    Shell.run("sysctl --system", check=False, timeout=30)


def set_defaults() -> None:
    """Outgoing always allowed (nothing the server itself connects to can hang), incoming closed
    except the allowed ports."""
    Shell.run("ufw default allow outgoing", check=False, timeout=30)
    Shell.run("ufw default deny incoming", check=False, timeout=30)


def allow_dns() -> None:
    for proto in ("tcp", "udp"):
        Shell.run(f"ufw allow 53/{proto}", check=False, timeout=30)


def enable() -> None:
    """Enable ufw. Call only after SSH and the service ports are allowed."""
    if not is_active():
        Shell.run("ufw --force enable", check=False, timeout=30)
        log.info("ufw enabled")
