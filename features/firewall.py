from core import iptables
from core.config import HTTP_PORTS, HTTPS_PORTS, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    description = "iptables firewall (optional): just opens SSH, DNS and the relay ports; turn on from the web panel"
    depends_on = []
    optional = True
    priority = 0     # first thing an install does

    def is_installed(self) -> bool:
        return iptables.available() and SSH_PORT_DEFAULT in iptables.allowed_tcp_ports()

    def install(self) -> None:
        log.info("Opening ports with iptables (rules are only added; nothing is flushed or removed) ...")
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get install -y iptables iptables-persistent",
                  check=False, timeout=180)

        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        http_ports = HTTP_PORTS | set(data.get("custom_http_ports", []))
        https_ports = HTTPS_PORTS | set(data.get("custom_https_ports", []))

        iptables.tune_conntrack()    # opt-in only (SSHAUTO_TUNE_CONNTRACK=1)
        for port in sorted({ssh_port, SSH_PORT_DEFAULT}):
            iptables.allow_tcp(port)
        iptables.allow_dns()
        iptables.ensure_ports(http_ports | https_ports | {ssh_port})
        iptables.save()

        log.success("iptables: SSH, DNS (53) and relay ports opened and saved.")

    def remove(self) -> None:
        log.info("iptables rules left untouched.")
