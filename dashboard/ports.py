from __future__ import annotations
from core.config import HTTP_PORTS, HTTPS_PORTS, state
from core.exceptions import ValidationError
from core.logger import log
from core.shell import Shell

class PortManager:
    def add(self, port, kind):
        self._validate(port, kind)
        data = state.ensure_defaults()
        key = "custom_http_ports" if kind == "http" else "custom_https_ports"
        ports = set(data.get(key, []))
        if port in ports or port in (HTTP_PORTS if kind == "http" else HTTPS_PORTS):
            raise ValidationError(f"Port {port} is already assigned.")
        ports.add(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply_firewall(port, kind, "add")
        self._apply_nginx()
        log.success(f"Added custom {kind.upper()} port {port}")

    def remove(self, port, kind):
        data = state.ensure_defaults()
        key = "custom_http_ports" if kind == "http" else "custom_https_ports"
        ports = set(data.get(key, []))
        if port not in ports:
            raise ValidationError(f"{port} is not a custom active port.")
        ports.discard(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply_firewall(port, kind, "remove")
        self._apply_nginx()
        log.success(f"Removed custom {kind.upper()} port {port}")

    def list_all(self):
        data = state.ensure_defaults()
        return {
            "http": sorted(HTTP_PORTS | set(data.get("custom_http_ports", []))),
            "https": sorted(HTTPS_PORTS | set(data.get("custom_https_ports", []))),
        }

    def _validate(self, port, kind):
        if kind not in ("http", "https"):
            raise ValidationError("kind must be 'http' or 'https'")
        if not (1 <= port <= 65535):
            raise ValidationError("port must be between 1 and 65535")

    def _apply_firewall(self, port, kind, action):
        if action == "add":
            Shell.run(f"iptables -I INPUT -p tcp --dport {port} -j ACCEPT", check=False)
        else:
            Shell.run(f"iptables -D INPUT -p tcp --dport {port} -j ACCEPT", check=False)

    def _apply_nginx(self):
        try:
            from features.nginx_relay import NginxRelayFeature
            NginxRelayFeature().install()
        except Exception as exc:
            log.error(f"Failed to update nginx: {exc}")
