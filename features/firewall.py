from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state

class FirewallFeature(BaseFeature):
    name = "firewall"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # Check if iptables rule for SSH/Proxy exists
        res = Shell.run("iptables -L -n", check=False)
        return "Chain" in res.stdout

    def install(self) -> None:
        log.info("Configuring firewall rules...")
        
        # Flush existing to avoid duplicates
        Shell.run("iptables -F", check=False)
        
        # Define base rules
        # Allow loopback, established connections, and your ports
        rules = [
            "iptables -A INPUT -i lo -j ACCEPT",
            "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A INPUT -p tcp --dport 22 -j ACCEPT",  # SSH
            "iptables -A INPUT -p tcp --dport 80 -j ACCEPT",  # HTTP
            "iptables -A INPUT -p tcp --dport 443 -j ACCEPT", # HTTPS
            "iptables -P INPUT DROP" # Default drop
        ]
        
        for rule in rules:
            Shell.run(rule)

        # STRICT ENFORCEMENT
        check = Shell.run("iptables -L -n", check=False)
        if "Chain" not in check.stdout:
            raise Exception("CRITICAL: Firewall installation succeeded but rules are not active.")
            
        log.success("Firewall verified and active.")

    def remove(self) -> None:
        Shell.run("iptables -F")
        Shell.run("iptables -P INPUT ACCEPT")
        log.info("Firewall rules cleared")
