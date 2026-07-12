"""
Single source of truth for every constant used across the project, plus
a tiny JSON-backed StateStore so features/dashboard can persist things.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# ----------------------------------------------------------------------
# Network layout aligned with Nginx WebSocket Architecture
# ----------------------------------------------------------------------
HTTP_PORTS = {80, 8080, 8880}
HTTPS_PORTS = {8443, 2096}

SSH_PORT_DEFAULT = 22                 # Real OpenSSH, direct access
DROPBEAR_PORT_DEFAULT = 110           # Dropbear backend isolated on localhost

# ----------------------------------------------------------------------
# Filesystem paths
# ----------------------------------------------------------------------
APP_ROOT = Path("/opt/sshauto")
STATE_FILE = APP_ROOT / "data" / "state.json"
LOG_DIR = Path("/var/log/sshauto")

NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_SITES_ENABLED = Path("/etc/nginx/sites-enabled")
NGINX_RELAY_NAME = "sshauto-relay"

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSH_BANNER_PATH = Path("/etc/ssh/sshauto_banner")

DROPBEAR_DEFAULTS_FILE = Path("/etc/default/dropbear")
DROPBEAR_BANNER_PATH = Path("/etc/ssh/dropbear_banner")

FAIL2BAN_FILTER_DIR = Path("/etc/fail2ban/filter.d")
FAIL2BAN_JAIL_LOCAL = Path("/etc/fail2ban/jail.local")

SYSTEMD_DIR = Path("/etc/systemd/system")
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
SSHAUTO_CERT_DIR = APP_ROOT / "certs"

GIT_POLL_INTERVAL_SECONDS = 30
USER_GROUP = "sshauto_tunnels"


class StateStore:
    """Thread-safe JSON file store for persistent operations status."""
    def __init__(self, path: Path = STATE_FILE):
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
