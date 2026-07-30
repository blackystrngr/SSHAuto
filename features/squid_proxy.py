from __future__ import annotations

import subprocess
from pathlib import Path
from core.logger import log
from core.shell import Shell
from core.config import state
from features.base import BaseFeature
import time

SQUID_CONF = Path("/etc/squid/squid.conf")
PASSWD_FILE = Path("/etc/squid/passwd")


class SquidProxyFeature(BaseFeature):
    name = "squid_proxy"
    description = "Install Squid proxy with username/password authentication"
    depends_on = ["packages", "firewall"]

    def is_installed(self) -> bool:
        return SQUID_CONF.exists() and Shell.run("systemctl is-active squid", check=False).ok

    def install(self) -> None:
        log.info("Installing Squid proxy with authentication...")

        # Install squid and apache2-utils (for htpasswd)
        Shell.run("apt-get install -y squid apache2-utils", check=True)

        data = state.ensure_defaults()
        user = data.get("squid_user", "admin")
        password = data.get("squid_password", "squid")

        # Create password file
        log.info(f"Creating password file for user: {user}")
        # Use --force to overwrite if exists
        Shell.run(f"htpasswd -bc {PASSWD_FILE} {user} {password}", check=True)
        PASSWD_FILE.chmod(0o600)

        # Write squid config with authentication
        config = f"""
# Squid config with basic authentication
http_port 0.0.0.0:3128

# Authentication
auth_param basic program /usr/lib/squid/basic_ncsa_auth {PASSWD_FILE}
auth_param basic realm Squid Proxy
auth_param basic credentialsttl 2 hours

acl authenticated proxy_auth REQUIRED
http_access allow authenticated

# Deny all other requests
http_access deny all

# Performance
cache deny all
forwarded_for delete
request_header_access X-Forwarded-For deny all
"""
        SQUID_CONF.write_text(config)
        log.info("Squid configured with authentication.")

        # Test and restart
        test_result = Shell.run("squid -k parse", check=False, timeout=10)
        if not test_result.ok:
            log.error(f"Squid config test failed: {test_result.stderr}")
            raise Exception("Squid configuration invalid.")

        Shell.run("systemctl reset-failed squid", check=False)
        Shell.run("systemctl enable squid", check=False)

        for attempt in range(3):
            result = Shell.run("systemctl restart squid", check=False, timeout=30)
            if result.ok:
                break
            time.sleep(2)
        else:
            log.warning("Squid service did not start cleanly.")

        time.sleep(2)
        if Shell.run("systemctl is-active squid", check=False).ok:
            log.success("Squid proxy installed and running with authentication.")
            log.important(f"Squid credentials:\n  Username: {user}\n  Password: {password}")
            log.important("To change password: sudo htpasswd /etc/squid/passwd <username>")
        else:
            log.warning("Squid service not active. Check logs with 'journalctl -u squid'.")

    def remove(self) -> None:
        Shell.run("systemctl stop squid", check=False)
        Shell.run("systemctl disable squid", check=False)
        SQUID_CONF.unlink(missing_ok=True)
        PASSWD_FILE.unlink(missing_ok=True)
        log.info("Squid removed.")
