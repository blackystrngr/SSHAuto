"""
Single source of truth for every constant used across the project.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
import requests

# ----------------------------------------------------------------------
# Project root – auto‑detect
# ----------------------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent.parent

# ----------------------------------------------------------------------
# Network layout
# ----------------------------------------------------------------------
HTTP_PORTS = {80, 8080, 8880, 2052, 2082, 2086, 2095}
HTTPS_PORTS = {443, 8443, 2053, 2083, 2087, 2096}

SSH_PORT_DEFAULT = 22
DROPBEAR_PORT_DEFAULT = 110
PROXY_PORT_DEFAULT = 9955
SQUID_PORT_DEFAULT = 3128

# New tunnel defaults
HYSTERIA_PORT_DEFAULT = 8443
HYSTERIA_DOMAIN_DEFAULT = "ns1.hi.blackstrngr.qzz.io"
HYSTERIA_PASSWORD_DEFAULT = "helloworld"

DNS_TUNNEL_PORT_DEFAULT = 5300
DNS_TUNNEL_DOMAIN_DEFAULT = "ns1.hi.blackstrngr.qzz.io"
DNS_TUNNEL_PASSWORD_DEFAULT = "helloworld"

ICMP_TUNNEL_KEY_DEFAULT = 123456

USER_GROUP = "sshauto-users"
GIT_POLL_INTERVAL_SECONDS = 30

# ----------------------------------------------------------------------
# Package Management
# ----------------------------------------------------------------------
REQUIRED_PACKAGES = [
    "nginx", "dropbear", "fail2ban", "iptables", "curl", "git",
    "certbot", "squid", "sslh", "cron", "iodine",
    "build-essential", "libpcap-dev", "wget", "ufw", "golang-go"
]
REMOVE_PACKAGES = ["apache2", "ufw", "firewalld"]
PIP_PACKAGES = []

# ----------------------------------------------------------------------
# Filesystem paths
# ----------------------------------------------------------------------
NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_SITES_ENABLED = Path("/etc/nginx/sites-enabled")
NGINX_RELAY_NAME = "sshauto-relay"

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSH_BANNER_PATH = Path("/etc/ssh/sshd_banner")
DROPBEAR_BANNER_PATH = Path("/etc/dropbear/banner")
DROPBEAR_DEFAULTS_FILE = Path("/etc/default/dropbear")
SSHAUTO_CERT_DIR = Path("/var/lib/sshauto/certs")
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")

LOG_DIR = Path("/var/log/sshauto")
SYSTEMD_DIR = Path("/etc/systemd/system")

FAIL2BAN_FILTER_DIR = Path("/etc/fail2ban/filter.d")
FAIL2BAN_JAIL_LOCAL = Path("/etc/fail2ban/jail.local")

SERVER_IP_DEFAULT = "your_server_ip"

def get_public_ip() -> str:
    """Auto‑detect the server's public IPv4 address."""
    try:
        ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
        if ip:
            return ip
    except Exception:
        pass
    try:
        ip = requests.get('https://ifconfig.me/ip', timeout=5).text.strip()
        if ip:
            return ip
    except Exception:
        pass
    return SERVER_IP_DEFAULT


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
            "squid_port": SQUID_PORT_DEFAULT,
            "hysteria_port": HYSTERIA_PORT_DEFAULT,
            "hysteria_domain": HYSTERIA_DOMAIN_DEFAULT,
            "hysteria_password": HYSTERIA_PASSWORD_DEFAULT,
            "dns_tunnel_port": DNS_TUNNEL_PORT_DEFAULT,
            "dns_tunnel_domain": DNS_TUNNEL_DOMAIN_DEFAULT,
            "dns_tunnel_password": DNS_TUNNEL_PASSWORD_DEFAULT,
            "icmp_tunnel_key": ICMP_TUNNEL_KEY_DEFAULT,
            "server_ip": get_public_ip(),
            "custom_http_ports": [],
            "custom_https_ports": [],
            "cert_strategy": None,
            "cert_domain": "hi.blackstrngr.qzz.io",
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
