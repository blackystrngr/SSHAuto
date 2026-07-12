"""
fail2ban protects both the direct OpenSSH port and the websocket relay
path. Dropbear needs a small custom filter since fail2ban doesn't ship
one out of the box on most distros.
"""
from __future__ import annotations

from core.config import FAIL2BAN_FILTER_DIR, FAIL2BAN_JAIL_LOCAL, SSH_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

DROPBEAR_FILTER = """[Definition]
failregex = ^.*[Bb]ad password attempt for .* from <HOST>.*$
            ^.*[Ll]ogin attempt for nonexistent user from <HOST>.*$
            ^.*[Ee]xit before auth from <HOST>.*$
ignoreregex =
"""


class Fail2banServiceFeature(BaseFeature):
    name = "fail2ban_service"
    description = "Set up fail2ban jails for ssh, dropbear, and nginx"
    depends_on = ["packages", "ssh_service", "dropbear_service"]

    def is_installed(self) -> bool:
        return FAIL2BAN_JAIL_LOCAL.exists() and "[sshauto-dropbear]" in FAIL2BAN_JAIL_LOCAL.read_text()

    def install(self) -> None:
        FAIL2BAN_FILTER_DIR.mkdir(parents=True, exist_ok=True)
        (FAIL2BAN_FILTER_DIR / "sshauto-dropbear.conf").write_text(DROPBEAR_FILTER)

        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        dropbear_log = self._detect_dropbear_log()

        jail_conf = f"""# Managed by sshauto
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
banaction = iptables-multiport

[sshd]
enabled  = true
port     = {ssh_port}
filter   = sshd
logpath  = %(sshd_log)s
maxretry = 4

[sshauto-dropbear]
enabled  = true
filter   = sshauto-dropbear
logpath  = {dropbear_log}
port     = anyport
maxretry = 5

[nginx-limit-req]
enabled  = false
"""
        FAIL2BAN_JAIL_LOCAL.write_text(jail_conf)
        Shell.run("systemctl enable fail2ban", check=False)
        Shell.run("systemctl restart fail2ban")
        log.success("fail2ban active: guarding sshd + dropbear")

    def remove(self) -> None:
        Shell.run("systemctl stop fail2ban", check=False)
        Shell.run("systemctl disable fail2ban", check=False)

    def _detect_dropbear_log(self) -> str:
        for candidate in ("/var/log/auth.log", "/var/log/secure"):
            if Shell.run(f"test -f {candidate}", check=False).ok:
                return candidate
        return "/var/log/auth.log"
