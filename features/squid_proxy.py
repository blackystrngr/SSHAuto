from __future__ import annotations

import hashlib
from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature
import time

SQUID_CONF = Path("/etc/squid/squid.conf")
SQUID_BACKUP = SQUID_CONF.with_suffix(".conf.bak")


class SquidProxyFeature(BaseFeature):
    name = "squid_proxy"
    description = "Install Squid proxy – no authentication, public"
    depends_on = ["packages", "firewall"]

    def is_installed(self) -> bool:
        return SQUID_CONF.exists() and Shell.run("systemctl is-active squid", check=False).ok

    def install(self) -> None:
        log.info("Installing Squid proxy...")
        Shell.run("apt-get install -y squid", check=True)

        # Minimal config (no auth)
        config = """
# Minimal Squid config – public CONNECT proxy
http_port 0.0.0.0:3128
acl SSL_ports port 1-65535
acl CONNECT method CONNECT
http_access allow CONNECT SSL_ports
http_access allow all
cache deny all
forwarded_for delete
request_header_access X-Forwarded-For deny all
"""
        # Check if config has changed
        current_config = config.strip()
        if SQUID_CONF.exists():
            old_config = SQUID_CONF.read_text().strip()
            if old_config == current_config:
                log.info("Squid config unchanged – skipping restart.")
                return
            # Backup old config
            SQUID_CONF.rename(SQUID_BACKUP)

        # Write new config
        SQUID_CONF.write_text(config)
        log.info("Squid config updated.")

        # Test config
        test = Shell.run("squid -k parse", check=False, timeout=10)
        if not test.ok:
            log.error(f"Squid config test failed: {test.stderr}")
            # Restore backup if exists
            if SQUID_BACKUP.exists():
                SQUID_BACKUP.rename(SQUID_CONF)
            raise Exception("Squid config invalid.")

        # Restart only if it was running or config changed
        if Shell.run("systemctl is-active squid", check=False).ok:
            log.info("Restarting Squid to apply new config...")
            Shell.run("systemctl restart squid", check=False, timeout=30)
        else:
            log.info("Starting Squid...")
            Shell.run("systemctl start squid", check=False, timeout=30)

        Shell.run("systemctl enable squid", check=False)

        # Verify
        if Shell.run("systemctl is-active squid", check=False).ok:
            log.success("Squid running on port 3128 (no auth).")
        else:
            log.warning("Squid not active. Check 'journalctl -u squid'.")

    def remove(self) -> None:
        Shell.run("systemctl stop squid", check=False)
        Shell.run("systemctl disable squid", check=False)
        SQUID_CONF.unlink(missing_ok=True)
        SQUID_BACKUP.unlink(missing_ok=True)
        log.info("Squid removed.")
