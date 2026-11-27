from __future__ import annotations

import datetime
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
        self._ensure_shells()

    def _ensure_group(self):
        Shell.run(f"getent group {USER_GROUP} || groupadd {USER_GROUP}", check=False)

    def _ensure_shells(self):
        shells = ["/bin/false", "/sbin/nologin", "/usr/sbin/nologin"]
        for sh in shells:
            if sh not in Shell.run("cat /etc/shells", check=False).stdout:
                Shell.run(f"echo {sh} >> /etc/shells", check=False)

    def create(self, username, password, expire_days=30):
        self.validate_username(username)
        self.validate_password(password)
        if Shell.run(["id", "-u", username], check=False).ok:
            raise ValidationError(f"user '{username}' already exists")
        shell = self._tunnel_shell()
        Shell.run(["useradd", "-m", "-s", shell, "-G", USER_GROUP, username])
        # password goes through stdin, never through a shell string
        done = Shell.run("chpasswd", check=False, input_text=f"{username}:{password}\n")
        if not done.ok:
            Shell.run(["userdel", "-r", username], check=False)
            raise ValidationError("could not set the password", hint=done.stderr.strip() or None)
        Shell.run(["passwd", "-u", username], check=False)
        self.set_expiry(username, expire_days, _checked=True)
        log.success(f"created user '{username}'")
        return SSHUser(username, self._expiry_of(username), False)

    def delete(self, username):
        self._require_managed(username)
        if not Shell.run(["userdel", "-rf", username], check=False).ok:
            raise ValidationError(f"could not delete '{username}'")
        log.success(f"deleted user '{username}'")

    def set_expiry(self, username, days, _checked=False):
        """Account expiry `days` from today (0/None = never)."""
        if not _checked:
            self._require_managed(username)
        if days:
            when = (datetime.date.today() + datetime.timedelta(days=int(days))).isoformat()
        else:
            when = "-1"
        Shell.run(["chage", "-E", when, username], check=False)

    def lock(self, username):
        self._require_managed(username)
        Shell.run(["passwd", "-l", username], check=False)
        Shell.run(["pkill", "-KILL", "-u", username], check=False)

    def unlock(self, username):
        self._require_managed(username)
        Shell.run(["passwd", "-u", username], check=False)

    @staticmethod
    def validate_username(username):
        if not isinstance(username, str) or not USERNAME_RE.match(username):
            raise ValidationError("invalid username",
                                  hint="3-32 chars: lowercase letters, digits, _ and -, starting with a letter or _")

    @staticmethod
    def validate_password(password):
        if (not isinstance(password, str) or not 6 <= len(password) <= 64
                or any(ord(c) < 32 or ord(c) == 127 for c in password)):
            raise ValidationError("invalid password", hint="6-64 printable characters")

    def _require_managed(self, username):
        """Only accounts this tool created (tunnel group) may be touched."""
        self.validate_username(username)
        if username not in {u.username for u in self.list()}:
            raise ValidationError(f"'{username}' is not a tunnel user")

    def list(self):
        group_res = Shell.run(f"getent group {USER_GROUP}", check=False)
        if not group_res.ok:
            return []
        parts = group_res.stdout.strip().split(":")
        if len(parts) < 3:
            return []
        gid = parts[2]
        usernames = set(parts[3].split(",")) if len(parts) > 3 else set()
        passwd_res = Shell.run("getent passwd", check=False)
        if passwd_res.ok:
            for line in passwd_res.stdout.splitlines():
                p = line.split(":")
                if len(p) > 3 and p[3] == gid:
                    usernames.add(p[0])
        return [SSHUser(u, self._expiry_of(u), self._is_locked(u)) for u in sorted(usernames) if u]

    def _tunnel_shell(self):
        for candidate in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"):
            if Shell.run(f"test -x {candidate}", check=False).ok:
                return candidate
        return "/bin/false"

    def _expiry_of(self, username):
        result = Shell.run(["chage", "-l", username], check=False)
        if not result.ok:
            return "unknown"
        for line in result.stdout.splitlines():
            if "Account expires" in line:
                return line.split(":", 1)[1].strip()
        return "never"

    def _is_locked(self, username):
        result = Shell.run(["passwd", "-S", username], check=False)
        return result.ok and " L " in f" {result.stdout} "
