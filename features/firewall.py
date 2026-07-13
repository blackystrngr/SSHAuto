from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class FirewallFeature(BaseFeature):
    name = "firewall"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # Always report as not installed so we always flush on install
        return False

    def install(self) -> None:
        log.info("Flushing iptables rules (script‑compatible – no restrictions)...")
        for table in ["", "-t nat", "-t mangle"]:
            Shell.run(f"iptables {table} -F", check=False)
            Shell.run(f"iptables {table} -X", check=False)
        # Set default policies to ACCEPT
        Shell.run("iptables -P INPUT ACCEPT", check=False)
        Shell.run("iptables -P FORWARD ACCEPT", check=False)
        Shell.run("iptables -P OUTPUT ACCEPT", check=False)
        log.success("Firewall flushed – all traffic allowed (like standalone script).")

    def remove(self) -> None:
        # Same as install – just flush
        self.install()
