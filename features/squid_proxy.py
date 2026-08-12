from __future__ import annotations

from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
import time

SQUID_CONF = Path("/etc/squid/squid.conf")

class SquidProxyFeature(BaseFeature):
    name = "squid_proxy"
    description = "Install Squid proxy – no authentication, public"
    depends_on = ["packages", "firewall"]

    def is_installed(self) -> bool:
        return SQUID_CONF.exists() and Shell.run("systemctl is-active squid", check=False).ok

    def install(self) -> None:
        log.info("Installing Squid proxy (no auth)...")
        Shell.run("apt-get install -y squid", check=True)

        if SQUID_CONF.exists():
            SQUID_CONF.rename(SQUID_CONF.with_suffix(".conf.bak"))

        config = """
# Squid config – public forward proxy on 0.0.0.0:3128
http_port 0.0.0.0:3128

# Allow CONNECT to any port
acl SSL_ports port 1-65535
acl CONNECT method CONNECT
http_access allow CONNECT SSL_ports

# Allow all other requests
http_access allow all

# Performance
cache deny all
forwarded_for delete
request_header_access X-Forwarded-For deny all
"""
        SQUID_CONF.write_text(config)
        log.info("Squid configured (no authentication).")

        test_result = Shell.run("squid -k parse", check=False, timeout=10)
        if not test_result.ok:
            log.error(f"Squid config test failed: {test_result.stderr}")
            raise Exception("Squid configuration invalid.")

        Shell.run("systemctl reset-failed squid", check=False)
        Shell.run("systemctl enable squid", check=False)

        for attempt in range(3):

        if Shell.run("systemctl is-active squid", check=False).ok:
            log.success("Squid proxy running on port 3128 (no auth).")
        else:
            log.warning("Squid service not active. Check 'journalctl -u squid'.")

    def remove(self) -> None:
        """Uninstall Squid and remove configuration."""
        Shell.run("systemctl stop squid", check=False)
        Shell.run("systemctl disable squid", check=False)
        SQUID_CONF.unlink(missing_ok=True)
        log.info("Squid removed.")
