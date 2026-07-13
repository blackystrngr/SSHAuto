"""
Configures the real OpenSSH daemon with speed‑optimised ciphers.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from core.config import APP_ROOT, SSHD_CONFIG, SSH_BANNER_PATH, SSH_PORT_DEFAULT, state
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

BANNERS_FILE = APP_ROOT / "data" / "banners.txt"

# Speed‑optimised cipher, MAC, and KEX settings
HARDENING_DIRECTIVES = {
    "PermitRootLogin": "prohibit-password",
    "PasswordAuthentication": "yes",   # dropbear/relay users still need password auth
    "X11Forwarding": "no",
    "ClientAliveInterval": "60",
    "ClientAliveCountMax": "3",
    "MaxAuthTries": "4",
    "LoginGraceTime": "20",
    # --- Speed optimisations ---
    "Ciphers": "chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com",
    "MACs": "umac-128-etm@openssh.com",
    "KexAlgorithms": "curve25519-sha256@libssh.org,ecdh-sha2-nistp256",
}


class SSHServiceFeature(BaseFeature):
    name = "ssh_service"
    description = "Configure OpenSSH: port, banner, hardening + fast ciphers"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return SSH_BANNER_PATH.exists() and "Banner" in SSHD_CONFIG.read_text()

    def install(self) -> None:
        if not SSHD_CONFIG.exists():
            raise ConfigError(f"{SSHD_CONFIG} not found — is openssh-server installed?")

        banner = self._pick_random_banner()
        SSH_BANNER_PATH.write_text(banner + "\n")
        log.info(f"wrote random banner ({len(banner.splitlines())} lines) to {SSH_BANNER_PATH}")

        data = state.ensure_defaults()
        port = data.get("ssh_port", SSH_PORT_DEFAULT)

        directives = dict(HARDENING_DIRECTIVES)
        directives["Port"] = str(port)
        directives["Banner"] = str(SSH_BANNER_PATH)

        self._apply_directives(directives)
        Shell.run("sshd -t")  # validate config
        Shell.run("systemctl restart ssh || systemctl restart sshd")
        log.success(f"sshd listening on port {port} with fast ciphers")

    def remove(self) -> None:
        log.warning("ssh_service.remove() only strips our directives, "
                     "it does not uninstall openssh-server")
        text = SSHD_CONFIG.read_text()
        for key in list(HARDENING_DIRECTIVES) + ["Banner"]:
            text = re.sub(rf"(?m)^{key}\s+.*$\n?", "", text)
        SSHD_CONFIG.write_text(text)
        Shell.run("systemctl restart ssh || systemctl restart sshd", check=False)

    # -- helpers ------------------------------------------------------
    def _pick_random_banner(self) -> str:
        if not BANNERS_FILE.exists():
            return "Authorized access only. All activity is logged."
        raw = BANNERS_FILE.read_text()
        options = [b.strip() for b in raw.split("---BANNER---") if b.strip()]
        return random.choice(options) if options else "Authorized access only."

    def _apply_directives(self, directives: dict[str, str]):
        text = SSHD_CONFIG.read_text()
        for key, value in directives.items():
            pattern = re.compile(rf"(?m)^#?\s*{re.escape(key)}\s+.*$")
            line = f"{key} {value}"
            if pattern.search(text):
                text = pattern.sub(line, text, count=1)
            else:
                text = text.rstrip() + f"\n{line}\n"
        SSHD_CONFIG.write_text(text)
