"""
Firewall policy updated for SSHAuto:
  - Flushes all existing rules completely.
  - Allows loopback traffic and established connections.
  - Opens explicit HTTP ports (80, 8080, 8880) and HTTPS ports (8443, 2096).
  - Drops everything else on INPUT/FORWARD.
"""
from __future__ import annotations

from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    description = "Flush iptables and configure explicit proxy ports"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        result = Shell.run("iptables -S INPUT", check=False)
        return result.ok and "DROP" in result.stdout

    def install(self) -> None:
        # Explicit target ports requested by the architecture
        http_ports = [80, 8080, 8880]
        https_ports = [8443, 2096]
        all_ports = sorted(http_ports + https_ports)

        log.info("Flushing all existing iptables rules across all tables...")
        for table in ("filter", "nat", "mangle"):
            Shell.run(f"iptables -t {table} -F", check=False)
            Shell.run(f"iptables -t {table} -X", check=False)

        base_rules = [
            "iptables -P INPUT DROP",
            "iptables -P FORWARD DROP",
            "iptables -P OUTPUT ACCEPT",
            "iptables -A INPUT -i lo -j ACCEPT",
            "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT",
        ]
        
        # Open explicit TCP ports for the Nginx front-end relay
        for port in all_ports:
            base_rules.append(f"iptables -A INPUT -p tcp --dport {port} -j ACCEPT")

        for rule in base_rules:
            Shell.run(rule)
            
        log.success(f"IPv4: Configured default DROP policy and allowed relay ports: {all_ports}")

        self._block_ipv6()
        self._persist()

    def remove(self) -> None:
        log.warning("Resetting iptables to default ACCEPT policies (Firewall disabled)")
        for table in ("filter", "nat", "mangle"):
            for chain in ("INPUT", "FORWARD", "OUTPUT"):
                Shell.run(f"iptables -t {table} -P {chain} ACCEPT", check=False)
                Shell.run(f"iptables -t {table} -F {chain}", check=False)

    def _block_ipv6(self):
        log.info("Enforcing system-wide IPv6 block (ip6tables drop + kernel sysctl)")
        Shell.run("ip6tables -F", check=False)
        for rule in ("ip6tables -P INPUT DROP", "ip6tables -P FORWARD DROP", "ip6tables -P OUTPUT DROP"):
            Shell.run(rule, check=False)
            
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
            log.warning(f"Could not write sysctl drop-in: {exc}")

    def _persist(self):
        if Shell.exists("netfilter-persistent"):
            Shell.run("netfilter-persistent save", check=False)
            return
        Shell.run("mkdir -p /etc/iptables", check=False)
        Shell.run("sh -c 'iptables-save > /etc/iptables/rules.v4'", check=False)
        Shell.run("sh -c 'ip6tables-save > /etc/iptables/rules.v6'", check=False)
