from core.config import HTTP_PORTS, HTTPS_PORTS, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class FirewallFeature(BaseFeature):
    name = "firewall"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # Check if at least our SSH rule exists
        res = Shell.run("iptables -C INPUT -p tcp --dport 22 -j ACCEPT", check=False)
        return res.ok

    def install(self) -> None:
        log.info("Adding firewall rules (no flush) ...")

        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
        https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))
        all_ports = set(http_ports + https_ports + [ssh_port])

        for port in all_ports:
            Shell.run(f"iptables -I INPUT -p tcp --dport {port} -j ACCEPT", check=False)

        # Allow loopback and established connections
        Shell.run("iptables -I INPUT -i lo -j ACCEPT", check=False)
        Shell.run("iptables -I INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT", check=False)

        log.success("Firewall rules added (existing rules preserved).")
        log.important("If you use a VPN, ensure its ports are also allowed.")

    def remove(self) -> None:
        # Only the user-added custom relay ports are closed. The base HTTP/HTTPS/SSH rules,
        # loopback and established rules are left alone: other services may rely on them.
        data = state.load()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        ports = set(data.get("custom_http_ports", [])) | set(data.get("custom_https_ports", []))
        for port in sorted(ports - {ssh_port, SSH_PORT_DEFAULT}):
            while Shell.run(f"iptables -D INPUT -p tcp --dport {port} -j ACCEPT", check=False).ok:
                pass
        log.info("Custom relay port rules removed (base ports left open on purpose).")
