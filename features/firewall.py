from core import ufw
from core.config import HTTP_PORTS, HTTPS_PORTS, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return ufw.is_active() and SSH_PORT_DEFAULT in ufw.allowed_tcp_ports()

    def install(self) -> None:
        log.info("Configuring ufw (rules are only added, never reset or deleted) ...")
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get install -y ufw", check=False, timeout=180)

        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        http_ports = HTTP_PORTS | set(data.get("custom_http_ports", []))
        https_ports = HTTPS_PORTS | set(data.get("custom_https_ports", []))

        # SSH first so enabling ufw can never lock us out
        ufw.ensure_enabled({ssh_port, SSH_PORT_DEFAULT})
        ufw.ensure_ports(http_ports | https_ports | {ssh_port})

        log.success("ufw active; relay and SSH ports allowed.")
        log.important("If you use a VPN, ensure its ports are also allowed (ufw allow <port>/tcp).")

    def remove(self) -> None:
        # ufw rules and state are never reset, disabled or deleted, not even on uninstall.
        log.info("ufw rules left untouched.")
