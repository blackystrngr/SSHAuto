from __future__ import annotations

from core.config import state, HTTP_PORTS, HTTPS_PORTS  #
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature  #

class FirewallFeature(BaseFeature):
    name = "firewall"
    description = "Configure safe, non-destructive iptables rules"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # Check if netfilter-persistent or our custom rule baseline exists
        return Shell.run("iptables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT", check=False) == 0

    def install(self) -> None:
        data = state.ensure_defaults()  #
        ssh_port = data.get("ssh_port", 22)  #
        
        log.info("Applying defensive firewall rules...")

        # ------------------------------------------------------------------
        # STEP 1: DEFENSIVE LAYER
        # Set all default policies to ACCEPT. If the script flushes rules 
        # while policies are ACCEPT, network traffic keeps flowing.
        # ------------------------------------------------------------------
        Shell.run("iptables -P INPUT ACCEPT")
        Shell.run("iptables -P FORWARD ACCEPT")
        Shell.run("iptables -P OUTPUT ACCEPT")

        # STEP 2: Clear old rule state safely
        Shell.run("iptables -F")
        Shell.run("iptables -X")

        # ------------------------------------------------------------------
        # STEP 3: VITAL CONNECTION WHITELISTS
        # ------------------------------------------------------------------
        # Allow the loopback interface (local process inter-communication)
        Shell.run("iptables -A INPUT -i lo -j ACCEPT")

        # CRITICAL: Keep your active cmd SSH session alive
        Shell.run("iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")

        # Open the standard SSH administration port
        Shell.run(f"iptables -A INPUT -p tcp --dport {ssh_port} -j ACCEPT")

        # ------------------------------------------------------------------
        # STEP 4: OPEN INJECTOR RELAY PORTS
        # ------------------------------------------------------------------
        # Union the default ports with any custom allocations from state store
        all_http = HTTP_PORTS.union(set(data.get("custom_http_ports", [])))  #
        all_https = HTTPS_PORTS.union(set(data.get("custom_https_ports", [])))  #

        for port in all_http:
            Shell.run(f"iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
        for port in all_https:
            Shell.run(f"iptables -A INPUT -p tcp --dport {port} -j ACCEPT")

        # ------------------------------------------------------------------
        # STEP 5: LOCKDOWN LAYER
        # Now that all rules are firmly in place, it is safe to close the door.
        # ------------------------------------------------------------------
        Shell.run("iptables -P INPUT DROP")
        Shell.run("iptables -P FORWARD DROP")

        # Make rules persistent across OS reboots
        Shell.run("netfilter-persistent save")
        log.success("Firewall rules synchronized successfully without terminal interruption.")

    def remove(self) -> None:
        log.info("Restoring wide-open firewall defaults...")
        Shell.run("iptables -P INPUT ACCEPT")
        Shell.run("iptables -P FORWARD ACCEPT")
        Shell.run("iptables -F")
        Shell.run("iptables -X")
        Shell.run("netfilter-persistent save")
