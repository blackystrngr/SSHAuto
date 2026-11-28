from __future__ import annotations

import base64
import glob
import hashlib
import shutil
from pathlib import Path

from core.config import (
    SQUID_PASS_DEFAULT,
    SQUID_PORT_DEFAULT,
    SQUID_USER_DEFAULT,
    state,
)
from core.exceptions import DependencyError, ValidationError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

SQUID_CONF = Path("/etc/squid/squid.conf")
SQUID_BACKUP = SQUID_CONF.with_suffix(".conf.sshauto-bak")
SQUID_PASSWD = Path("/etc/squid/sshauto_passwd")
MARKER = "# managed by sshauto"

HELPER_GLOBS = [
    "/usr/lib/squid/basic_ncsa_auth",
    "/usr/lib/squid3/basic_ncsa_auth",
    "/usr/lib*/squid*/basic_ncsa_auth",
]


def _find_helper() -> str:
    for pattern in HELPER_GLOBS:
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    raise DependencyError("squid basic_ncsa_auth helper not found",
                          hint="Is the 'squid' package installed correctly?")


def _validate(user: str, password: str, port: int) -> None:
    if not user or not password:
        raise ValidationError("squid username and password cannot be empty")
    for label, val in (("username", user), ("password", password)):
        if any(c.isspace() for c in val):
            raise ValidationError(f"squid {label} cannot contain whitespace")
    if ":" in user:
        raise ValidationError("squid username cannot contain ':'")
    if not (1 <= port <= 65535):
        raise ValidationError("squid port must be between 1 and 65535")


def _htpasswd_line(user: str, password: str) -> str:
    digest = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
    return f"{user}:{{SHA}}{digest}\n"


def _password_entries(user: str, password: str) -> list[str]:
    """Candidate htpasswd lines, most portable first. Some squid builds (e.g. GnuTLS ones on Ubuntu 24.04)
    do not understand {SHA}, so try apr1 / sha512 crypt (hashed via openssl, password on stdin) first."""
    entries = []
    for flag in ("-apr1", "-6"):
        res = Shell.run(f"openssl passwd {flag} -stdin", check=False, timeout=10,
                        input_text=f"{password}\n")
        digest = res.stdout.strip()
        if res.ok and digest.startswith("$"):
            entries.append(f"{user}:{digest}\n")
    entries.append(_htpasswd_line(user, password))
    return entries


def _fw_rule(action: str, port: int) -> None:
    """Add-only: ufw rules are never deleted ('del' is a no-op)."""
    if action != "add":
        return
    from core import ufw
    ufw.ensure_ports([port])


class SquidProxyFeature(BaseFeature):
    name = "squid_proxy"
    description = "Squid proxy (optional) – username/password required"
    depends_on = ["packages", "firewall"]
    optional = True

    def is_installed(self) -> bool:
        if not SQUID_CONF.exists() or MARKER not in SQUID_CONF.read_text():
            return False
        return Shell.run("systemctl is-active squid", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        user = data.get("squid_user") or SQUID_USER_DEFAULT
        password = data.get("squid_pass") or SQUID_PASS_DEFAULT
        port = int(data.get("squid_port") or SQUID_PORT_DEFAULT)
        _validate(user, password, port)

        log.info("Installing Squid proxy...")
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get install -y squid",
                  timeout=300, retries=1)
        helper = _find_helper()

        # password file (squid helper runs as user 'proxy'); use the first hash format the helper accepts
        check = None
        for entry in _password_entries(user, password):
            SQUID_PASSWD.write_text(entry)
            try:
                shutil.chown(SQUID_PASSWD, "root", "proxy")
            except (LookupError, OSError):
                pass
            SQUID_PASSWD.chmod(0o640)
            check = Shell.run(f"{helper} {SQUID_PASSWD}", check=False, timeout=10,
                              input_text=f"{user} {password}\n")
            if "OK" in check.stdout:
                break
        else:
            raise Exception(f"squid auth helper rejected the credentials: {check.stdout.strip()}")

        config = f"""{MARKER} – do not edit by hand
http_port 0.0.0.0:{port}

auth_param basic program {helper} {SQUID_PASSWD}
auth_param basic children 5
auth_param basic realm Proxy
auth_param basic credentialsttl 2 hours
acl authed proxy_auth REQUIRED

http_access allow authed
http_access deny all

cache deny all
forwarded_for delete
via off
"""
        # keep the distro's original config once
        if SQUID_CONF.exists() and MARKER not in SQUID_CONF.read_text() and not SQUID_BACKUP.exists():
            shutil.copy2(SQUID_CONF, SQUID_BACKUP)
        previous = SQUID_CONF.read_text() if SQUID_CONF.exists() else None
        SQUID_CONF.write_text(config)

        test = Shell.run("squid -k parse", check=False, timeout=20)
        if not test.ok:
            log.error(f"Squid config test failed: {test.stderr}")
            if previous is not None:
                SQUID_CONF.write_text(previous)
            raise Exception("Squid config invalid.")

        _fw_rule("add", port)
        Shell.run("systemctl enable squid", check=False)
        Shell.run("systemctl restart squid", check=False, timeout=60)

        if Shell.run("systemctl is-active squid", check=False).ok:
            log.success(f"Squid running on port {port} (auth required).")
            log.important(f"Squid login: {user} / {password}")
        else:
            raise Exception("Squid not active. Check 'journalctl -u squid'.")

    def remove(self) -> None:
        ours = SQUID_PASSWD.exists() or (SQUID_CONF.exists() and MARKER in SQUID_CONF.read_text())
        if not ours:
            return
        port = int(state.get("squid_port") or SQUID_PORT_DEFAULT)
        Shell.run("systemctl stop squid", check=False)
        Shell.run("systemctl disable squid", check=False)
        _fw_rule("del", port)
        if SQUID_BACKUP.exists():
            shutil.copy2(SQUID_BACKUP, SQUID_CONF)
            SQUID_BACKUP.unlink()
        elif SQUID_CONF.exists() and MARKER in SQUID_CONF.read_text():
            SQUID_CONF.unlink()
        SQUID_PASSWD.unlink(missing_ok=True)
        log.info("Squid proxy disabled (package left installed).")
