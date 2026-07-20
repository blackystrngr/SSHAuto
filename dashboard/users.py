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
        self._ensure_shells()

    def _ensure_group(self):
        Shell.run(f"getent group {USER_GROUP} || groupadd {USER_GROUP}", check=False)

    def _ensure_shells(self):
        shells = ["/bin/false", "/sbin/nologin", "/usr/sbin/nologin"]
        for sh in shells:
            if sh not in Shell.run("cat /etc/shells", check=False).stdout:
                Shell.run(f"echo {sh} >> /etc/shells", check=False)

    def create(self, username, password, expire_days=30):
        if not USERNAME_RE.match(username):
            raise ValidationError("invalid username")
        if Shell.run(f"id -u {username}", check=False).ok:
            raise ValidationError(f"user '{username}' already exists")
        shell = self._tunnel_shell()
        Shell.run(f"useradd -m -s {shell} -G {USER_GROUP} {username}")
        Shell.run(f"printf '%s:%s\\n' '{username}' '{password}' | chpasswd", check=False)
        Shell.run(f"passwd -u {username}", check=False)
        if expire_days:
            Shell.run(f"chage -M {expire_days} {username}", check=False)
        if self._is_locked(username):
            Shell.run(f"passwd -u {username}", check=False)
        log.success(f"created user '{username}'")
        return SSHUser(username, self._expiry_of(username), False)

    def delete(self, username):
        if not Shell.run(f"userdel -r {username}", check=False).ok:
            raise ValidationError(f"could not delete '{username}'")
        log.success(f"deleted user '{username}'")

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
        result = Shell.run(f"chage -l {username}", check=False)
        if not result.ok:
            return "unknown"
        for line in result.stdout.splitlines():
            if "Account expires" in line:
                return line.split(":", 1)[1].strip()
        return "never"

    def _is_locked(self, username):
        result = Shell.run(f"passwd -S {username}", check=False)
        return result.ok and " L " in f" {result.stdout} "
