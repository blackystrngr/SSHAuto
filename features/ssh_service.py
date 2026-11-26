from __future__ import annotations

import random
import re

from core.config import APP_ROOT, SSH_BANNER_PATH, SSH_PORT_DEFAULT, SSHD_CONFIG, state
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

BANNERS_FILE = APP_ROOT / "data" / "banners.txt"

HARDENING_DIRECTIVES = {
    "PermitRootLogin": "prohibit-password",
    "PasswordAuthentication": "yes",
    "X11Forwarding": "no",
    "ClientAliveInterval": "60",
    "ClientAliveCountMax": "3",
    "MaxAuthTries": "4",
    "LoginGraceTime": "20",
    # In HARDENING_DIRECTIVES, add:
    "UseDNS": "no",
}

class SSHServiceFeature(BaseFeature):
    name = "ssh_service"
    description = "Configure OpenSSH: port, banner, hardening"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return SSH_BANNER_PATH.exists() and "Banner" in SSHD_CONFIG.read_text()

    def install(self) -> None:
        if not SSHD_CONFIG.exists():
            raise ConfigError(f"{SSHD_CONFIG} not found")
        banner = self._pick_random_banner()
        SSH_BANNER_PATH.write_text(banner + "\n")
        log.info(f"wrote random banner to {SSH_BANNER_PATH}")
        data = state.ensure_defaults()
        port = data.get("ssh_port", SSH_PORT_DEFAULT)
        directives = dict(HARDENING_DIRECTIVES)
        directives["Port"] = str(port)
        directives["Banner"] = str(SSH_BANNER_PATH)
        self._apply_directives(directives)
        Shell.run("sshd -t")
        Shell.run("systemctl restart ssh || systemctl restart sshd")
        log.success(f"sshd listening on port {port}")

    def remove(self) -> None:
        if not SSHD_CONFIG.exists():
            return
        original = SSHD_CONFIG.read_text()
        text = original
        for key in list(HARDENING_DIRECTIVES) + ["Banner"]:
            text = re.sub(rf"(?m)^{key}\s+.*$\n?", "", text)
        SSHD_CONFIG.write_text(text)
        if not Shell.run("sshd -t", check=False).ok:
            SSHD_CONFIG.write_text(original)
            log.error("sshd config invalid after cleanup; original restored.")
            return
        SSH_BANNER_PATH.unlink(missing_ok=True)
        Shell.run("systemctl restart ssh || systemctl restart sshd", check=False)
        log.info("OpenSSH hardening and banner removed (Port left unchanged to avoid lockout).")

    def _pick_random_banner(self):
        if not BANNERS_FILE.exists():
            return "Authorized access only."
        raw = BANNERS_FILE.read_text()
        options = [b.strip() for b in raw.split("---BANNER---") if b.strip()]
        return random.choice(options) if options else "Authorized access only."

    def _apply_directives(self, directives):
        text = SSHD_CONFIG.read_text()
        for key, value in directives.items():
            pattern = re.compile(rf"(?m)^#?\s*{re.escape(key)}\s+.*$")
            line = f"{key} {value}"
            if pattern.search(text):
                text = pattern.sub(line, text, count=1)
            else:
                text = text.rstrip() + f"\n{line}\n"
        SSHD_CONFIG.write_text(text)
