"""
Manages the actual Linux accounts that authenticate through dropbear
(relayed by nginx) or directly through OpenSSH. Accounts get a
no-interactive-shell (tunnel only) and are tagged into USER_GROUP so the
dashboard can tell "our" accounts apart from normal system accounts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.config import USER_GROUP
from core.exceptions import ValidationError
from core.logger import log
from core.shell import Shell

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")


@dataclass
class SSHUser:
    username: str
    expires: str
    locked: bool


class UserManager:
    def __init__(self):
        self._ensure_group()

    def _ensure_group(self):
        Shell.run(f"getent group {USER_GROUP} || groupadd {USER_GROUP}", check=False)

    def create(self, username: str, password: str, expire_days: int | None = 30) -> SSHUser:
        if not USERNAME_RE.match(username):
            raise ValidationError(
                "invalid username",
                hint="use 3-32 lowercase letters/digits/underscore/hyphen, starting with a letter",
            )
        exists = Shell.run(f"id -u {username}", check=False)
        if exists.ok:
            raise ValidationError(f"user '{username}' already exists")

        shell = self._tunnel_shell()
        Shell.run(f"useradd -m -s {shell} -g {USER_GROUP} {username}")
        Shell.run(f"chpasswd", input_text=f"{username}:{password}\n")

        if expire_days:
            Shell.run(f"chage -M {expire_days} {username}", check=False)

        log.success(f"created tunnel user '{username}'"
                     + (f" (expires in {expire_days}d)" if expire_days else ""))
        return SSHUser(username, self._expiry_of(username), locked=False)

    def delete(self, username: str):
        result = Shell.run(f"userdel -r {username}", check=False)
        if not result.ok:
            raise ValidationError(f"could not delete '{username}': {result.stderr.strip()}")
        log.success(f"deleted user '{username}'")

    def list(self) -> list[SSHUser]:
        result = Shell.run(f"getent group {USER_GROUP}", check=False)
        if not result.ok or ":" not in result.stdout:
            return []
        members = result.stdout.strip().split(":")[-1]
        usernames = [u for u in members.split(",") if u]
        return [SSHUser(u, self._expiry_of(u), locked=self._is_locked(u)) for u in usernames]

    def _tunnel_shell(self) -> str:
        for candidate in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"):
            if Shell.run(f"test -x {candidate}", check=False).ok:
                return candidate
        return "/bin/false"

    def _expiry_of(self, username: str) -> str:
        result = Shell.run(f"chage -l {username}", check=False)
        if not result.ok:
            return "unknown"
        for line in result.stdout.splitlines():
            if line.lower().startswith("account expires"):
                return line.split(":", 1)[1].strip()
        return "never"

    def _is_locked(self, username: str) -> bool:
        result = Shell.run(f"passwd -S {username}", check=False)
        return result.ok and " L " in f" {result.stdout} "
