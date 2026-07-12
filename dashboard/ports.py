"""
Lets the operator add ports beyond the built-in HTTP_PORTS/HTTPS_PORTS
sets (e.g. a CDN provider that uses a different port range). Any change
here touches three things in lockstep: state.json, the nginx relay
config, and the firewall — handled together so they never drift apart.
"""
from __future__ import annotations

from core.config import HTTP_PORTS, HTTPS_PORTS, state
from core.exceptions import ValidationError
from core.logger import log


class PortManager:
    def add(self, port: int, kind: str) -> None:
        self._validate(port, kind)
        data = state.ensure_defaults()
        key = "custom_http_ports" if kind == "http" else "custom_https_ports"
        ports = set(data.get(key, []))
        if port in ports or port in (HTTP_PORTS if kind == "http" else HTTPS_PORTS):
            raise ValidationError(f"port {port} is already active")
        ports.add(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply()
        log.success(f"added custom {kind.upper()} port {port}")

    def remove(self, port: int, kind: str) -> None:
        data = state.ensure_defaults()
        key = "custom_http_ports" if kind == "http" else "custom_https_ports"
        ports = set(data.get(key, []))
        if port not in ports:
            raise ValidationError(f"{port} is not a custom port (built-in ports can't be removed)")
        ports.discard(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply()
        log.success(f"removed custom {kind.upper()} port {port}")

    def list_all(self) -> dict:
        data = state.ensure_defaults()
        return {
            "http": sorted(HTTP_PORTS | set(data.get("custom_http_ports", []))),
            "https": sorted(HTTPS_PORTS | set(data.get("custom_https_ports", []))),
        }

    def _validate(self, port: int, kind: str):
        if kind not in ("http", "https"):
            raise ValidationError("kind must be 'http' or 'https'")
        if not (1 <= port <= 65535):
            raise ValidationError("port must be between 1 and 65535")

    def _apply(self):
        # Re-render nginx and re-apply the firewall so the new port is both
        # relayed and actually reachable through iptables.
        from features.firewall import FirewallFeature
        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()
        FirewallFeature().install()
