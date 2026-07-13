"""
Single source of truth for every constant used across the project, plus
a tiny JSON-backed StateStore so features/dashboard can persist things
(current dropbear port, custom ports, chosen cert strategy, domain...)
without a database.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

# ----------------------------------------------------------------------
# Network layout
# ----------------------------------------------------------------------
HTTP_PORTS = {80, 8080, 8880, 2052, 2082, 2086, 2095}
HTTPS_PORTS = {443, 8443, 2053, 2083, 2087, 2096}
SSH_PORT_DEFAULT = 22          # real OpenSSH, direct access
DROPBEAR_PORT_DEFAULT = 113    # dropbear tunnel backend (matches config)
PROXY_PORT_DEFAULT = 8000      # Python asyncio proxy

USER_GROUP = "sshauto-users"

# Automation & polling timelines
GIT_POLL_INTERVAL_SECONDS = 30

# ----------------------------------------------------------------------
# Package Management Requirements
# ----------------------------------------------------------------------
REQUIRED_PACKAGES = ["nginx", "dropbear", "fail2ban", "iptables", "curl", "git"]
REMOVE_PACKAGES = ["apache2"]   # Prevents port 80 binding conflicts
PIP_PACKAGES = []               # Handled via requirements.txt

# ----------------------------------------------------------------------
# Filesystem paths (all as specified / conventional Debian-family paths)
# ----------------------------------------------------------------------
NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_SITES_ENABLED = Path("/etc/nginx/sites-enabled")
NGINX_RELAY_NAME = "sshauto-relay"

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSH_BANNER_PATH = Path("/etc/ssh/sshd_banner")
DROPBEAR_BANNER_PATH = Path("/etc/dropbear/banner")
DROPBEAR_DEFAULTS_FILE = Path("/etc/default/dropbear")

APP_ROOT = Path("/opt/sshauto")
SSHAUTO_CERT_DIR = Path("/var/lib/sshauto/certs")
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")

# System tracking & runtime directories
LOG_DIR = Path("/var/log/sshauto")
SYSTEMD_DIR = Path("/etc/systemd/system")

# Fail2ban service layouts
FAIL2BAN_FILTER_DIR = Path("/etc/fail2ban/filter.d")
FAIL2BAN_JAIL_LOCAL = Path("/etc/fail2ban/jail.local")


class StateStore:
    def __init__(self, path: Path = Path("/var/lib/sshauto/state.json")):
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}

    def load(self) -> dict:
        with self._lock:
            return self._read()

    def save(self, data: dict):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            tmp.replace(self.path)

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    def set(self, key: str, value):
        data = self.load()
        data[key] = value
        self.save(data)

    def defaults(self) -> dict:
        return {
            "ssh_port": SSH_PORT_DEFAULT,
            "dropbear_port": DROPBEAR_PORT_DEFAULT,
            "proxy_port": PROXY_PORT_DEFAULT,
            "custom_http_ports": [],
            "custom_https_ports": [],
            "cert_strategy": None,
            "cert_domain": None,
            "installed_features": [],
            "created_at": None,
        }

    def ensure_defaults(self):
        data = self.load()
        changed = False
        for k, v in self.defaults().items():
            if k not in data:
                data[k] = v
                changed = True
        if changed:
            self.save(data)
        return data


state = StateStore()
