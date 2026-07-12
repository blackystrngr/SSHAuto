"""
Single source of truth for every constant used across the project, plus
a tiny JSON-backed StateStore so features/dashboard can persist things
(current dropbear port, custom ports, chosen cert strategy, domain...)
without a database.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# ----------------------------------------------------------------------
# Network layout
# ----------------------------------------------------------------------
HTTP_PORTS = {80, 8080, 8880, 2052, 2082, 2086, 2095}
HTTPS_PORTS = {443, 8443, 2053, 2083, 2087, 2096}

SSH_PORT_DEFAULT = 22                 # real OpenSSH, direct access
DROPBEAR_PORT_DEFAULT = 143           # dropbear, bound to 127.0.0.1 only,
                                       # reached exclusively through the
                                       # nginx websocket relay above.

# ----------------------------------------------------------------------
# Filesystem paths (all as specified / conventional Debian-family paths)
# ----------------------------------------------------------------------
NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_SITES_ENABLED = Path("/etc/nginx/sites-enabled")
NGINX_RELAY_NAME = "sshauto-relay"

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSH_BANNER_PATH = Path("/etc/ssh/sshauto_banner")

DROPBEAR_DEFAULTS_FILE = Path("/etc/default/dropbear")
DROPBEAR_BANNER_PATH = Path("/etc/dropbear/sshauto_banner")

FAIL2BAN_JAIL_LOCAL = Path("/etc/fail2ban/jail.local")
FAIL2BAN_FILTER_DIR = Path("/etc/fail2ban/filter.d")

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
SSHAUTO_CERT_DIR = Path("/etc/sshauto/certs")

APP_ROOT = Path(os.environ.get("SSHAUTO_HOME", "/opt/sshauto"))
STATE_DIR = Path("/etc/sshauto")
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = Path("/var/log/sshauto")

SYSTEMD_DIR = Path("/etc/systemd/system")

# ----------------------------------------------------------------------
# Packages
# ----------------------------------------------------------------------
REQUIRED_PACKAGES = [
    "iptables",              # firewall (also provides ip6tables)
    "openssh-server",        # real ssh daemon
    "dropbear",              # lightweight ssh, relayed over websocket
    "nginx",                 # relay / reverse proxy
    "certbot",                # ACME client
    "python3",
    "python3-pip",
    "python3-venv",
    "curl",
    "wget",
    "git",                   # required by the auto-updater
    "fail2ban",               # brute-force protection
    "socat",                  # used by certbot standalone / acme flows
    "jq",                      # JSON parsing in shell helpers
    "net-tools",               # netstat, used as ss fallback
    "cron",
    "unzip",
    "openssl",
    "uuid-runtime",
    "dnsutils",               # dig, used for domain validation before ACME
]

REMOVE_PACKAGES = [
    "apache2",
    "apache2-bin",
    "apache2-utils",
    "apache2-data",
    "ufw",
    "firewalld",              # conflicts with our raw iptables rules
]

PIP_PACKAGES = [
    "requests>=2.31",
]

# ----------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------
GIT_POLL_INTERVAL_SECONDS = 30
USER_GROUP = "sshauto-users"   # marker group for accounts created by us


class StateStore:
    """
    Tiny JSON key/value store at /etc/sshauto/state.json.
    Thread-safe, atomic writes (write-tmp + rename).
    """

    _lock = threading.Lock()

    def __init__(self, path: Path = STATE_FILE):
        self.path = path

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
