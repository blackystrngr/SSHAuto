from features.base import BaseFeature
from core.shell import Shell
from core.logger import log
from core.config import state

class MultiplexingFeature(BaseFeature):
    name = "multiplexing"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # Check if the session limit is set to '0' (unlimited)
        res = Shell.run("grep -c 'MaxSessions 0' /etc/ssh/sshd_config", check=False)
        return res.stdout.strip() == "1"

    def install(self) -> None:
        log.info("Enabling multi-session support...")
        # Update sshd_config to allow unlimited sessions for multiplexing
        Shell.run("sed -i 's/^#MaxSessions.*/MaxSessions 0/' /etc/ssh/sshd_config || echo 'MaxSessions 0' >> /etc/ssh/sshd_config")
        Shell.run("systemctl restart ssh")
        log.success("Multiplexing support enabled (MaxSessions set to 0).")

    def remove(self) -> None:
        Shell.run("sed -i '/MaxSessions 0/d' /etc/ssh/sshd_config", check=False)
        Shell.run("systemctl restart ssh")
        log.info("Multiplexing support disabled.")
