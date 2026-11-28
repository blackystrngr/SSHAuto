from core import ufw
from core.config import HTTP_PORTS, HTTPS_PORTS, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    description = "ufw firewall (optional): SSH, DNS and the relay ports; turn on from the web panel"
    depends_on = []
    optional = True
    priority = 0     # first thing an install does

    def is_installed(self) -> bool:
        return ufw.is_active() and SSH_PORT_DEFAULT in ufw.allowed_tcp_ports()

    def install(self) -> None:
        log.info("Configuring ufw (rules are only added; no iptables flush) ...")
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get install -y ufw", check=False, timeout=180)

        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        http_ports = HTTP_PORTS | set(data.get("custom_http_ports", []))
        https_ports = HTTPS_PORTS | set(data.get("custom_https_ports", []))

        ufw.tune_conntrack()    # opt-in only (SSHAUTO_TUNE_CONNTRACK=1)
        ufw.set_defaults()
        # allow everything we need BEFORE enabling, so no connection (panel, tunnel, SSH) is cut
        for port in sorted({ssh_port, SSH_PORT_DEFAULT}):
            ufw.allow_tcp(port)
        ufw.allow_dns()
        ufw.ensure_ports(http_ports | https_ports | {ssh_port})
        ufw.enable()
        ufw.release_oracle_reject()

        log.success("ufw active; SSH, DNS (53) and relay ports allowed.")
        log.important("If you use a VPN, ensure its ports are also allowed (ufw allow <port>/tcp).")

    def remove(self) -> None:
        # ufw rules are left untouched on uninstall.
        log.info("ufw rules left untouched.")
