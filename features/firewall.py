from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class FirewallFeature(BaseFeature):
    name = "firewall"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return False

    def install(self) -> None:
        log.info("Flushing iptables and allowing all traffic.")
        for table in ["", "-t nat", "-t mangle"]:
            Shell.run(f"iptables {table} -F", check=False)
            Shell.run(f"iptables {table} -X", check=False)
        Shell.run("iptables -P INPUT ACCEPT", check=False)
        Shell.run("iptables -P FORWARD ACCEPT", check=False)
        Shell.run("iptables -P OUTPUT ACCEPT", check=False)
        log.success("Firewall: all traffic allowed.")

    def remove(self) -> None:
        self.install()
