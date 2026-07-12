"""
Firewall policy:
  - IPv4: default-deny INPUT, allow loopback/established, allow SSH port,
    dropbear port (loopback only - never exposed publicly), and every
    HTTP_PORTS/HTTPS_PORTS the relay listens on.
  - IPv6: dropped entirely (both via ip6tables default-deny AND disabling
    ipv6 at the kernel level, belt-and-suspenders since some panels only
    partially support ip6tables persistence).
"""
from __future__ import annotations

from core.config import DROPBEAR_PORT_DEFAULT, HTTP_PORTS, HTTPS_PORTS, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    description = "Configure iptables, block IPv6 entirely"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        result = Shell.run("iptables -S INPUT", check=False)
        return result.ok and "DROP" in result.stdout

    def install(self) -> None:
        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        all_ports = sorted(HTTP_PORTS | HTTPS_PORTS
                            | set(data.get("custom_http_ports", []))
                            | set(data.get("custom_https_ports", [])))

        log.info("flushing existing IPv4 rules")
        for chain in ("INPUT", "FORWARD", "OUTPUT"):
            Shell.run(f"iptables -F {chain}", check=False)

        base_rules = [
            "iptables -P INPUT DROP",
            "iptables -P FORWARD DROP",
            "iptables -P OUTPUT ACCEPT",
            "iptables -A INPUT -i lo -j ACCEPT",
            "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT",
            f"iptables -A INPUT -p tcp --dport {ssh_port} -j ACCEPT",
        ]
        for port in all_ports:
            base_rules.append(f"iptables -A INPUT -p tcp --dport {port} -j ACCEPT")

        for rule in base_rules:
            Shell.run(rule)
        log.success(f"IPv4: allowed ssh:{ssh_port} + {len(all_ports)} relay ports, "
                     "default DROP for everything else")

        # dropbear itself only ever binds 127.0.0.1, so it needs no public
        # firewall rule at all — that's the point of fronting it via nginx.
        log.debug(f"dropbear stays on loopback:{data.get('dropbear_port', DROPBEAR_PORT_DEFAULT)}, "
                   "not exposed by the firewall")

        self._block_ipv6()
        self._persist()

    def remove(self) -> None:
        log.warning("resetting iptables to ACCEPT-all (firewall disabled)")
        for chain in ("INPUT", "FORWARD", "OUTPUT"):
            Shell.run(f"iptables -P {chain} ACCEPT", check=False)
            Shell.run(f"iptables -F {chain}", check=False)

    def _block_ipv6(self):
        log.info("blocking IPv6 (ip6tables default-deny + kernel disable)")
        Shell.run("ip6tables -F", check=False)
        for rule in (
            "ip6tables -P INPUT DROP",
            "ip6tables -P FORWARD DROP",
            "ip6tables -P OUTPUT DROP",
        ):
            Shell.run(rule, check=False)
        # Defense in depth: also disable ipv6 at the kernel/sysctl level.
        sysctl_conf = (
            "net.ipv6.conf.all.disable_ipv6 = 1\n"
            "net.ipv6.conf.default.disable_ipv6 = 1\n"
            "net.ipv6.conf.lo.disable_ipv6 = 1\n"
        )
        try:
            with open("/etc/sysctl.d/99-sshauto-disable-ipv6.conf", "w") as f:
                f.write(sysctl_conf)
            Shell.run("sysctl --system", check=False)
        except OSError as exc:
            log.warning(f"could not write sysctl drop-in: {exc}")

    def _persist(self):
        # netfilter-persistent (from iptables-persistent) if present,
        # otherwise fall back to a manual save + a small restore unit.
        if Shell.exists("netfilter-persistent"):
            Shell.run("netfilter-persistent save", check=False)
            return
        Shell.run("mkdir -p /etc/iptables", check=False)
        Shell.run("sh -c 'iptables-save > /etc/iptables/rules.v4'", check=False)
        Shell.run("sh -c 'ip6tables-save > /etc/iptables/rules.v6'", check=False)
        log.debug("saved rules to /etc/iptables/rules.v4 (no netfilter-persistent found)")
