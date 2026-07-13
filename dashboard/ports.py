"""
Manages runtime port lists via state storage and hooks updates directly
into the newly restructured Nginx relay and Firewall components.
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
            raise ValidationError(f"Port {port} is already actively assigned.")

        ports.add(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply()
        log.success(f"Successfully appended custom {kind.upper()} listener on port {port}")

    def remove(self, port: int, kind: str) -> None:
        data = state.ensure_defaults()
        key = "custom_http_ports" if kind == "http" else "custom_https_ports"
        ports = set(data.get(key, []))

        if port not in ports:
            raise ValidationError(f"{port} is not a custom active port.")

        ports.discard(port)
        data[key] = sorted(ports)
        state.save(data)
        self._apply()
        log.success(f"Removed custom {kind.upper()} listener from port {port}")

    def list_all(self) -> dict:
        data = state.ensure_defaults()
        return {
            "http": sorted(HTTP_PORTS | set(data.get("custom_http_ports", []))),
            "https": sorted(HTTPS_PORTS | set(data.get("custom_https_ports", []))),
        }

    def _validate(self, port: int, kind: str):
        if kind not in ("http", "https"):
            raise ValidationError("Port connection type classification must be 'http' or 'https'")
        if not (1 <= port <= 65535):
            raise ValidationError("System target network port boundary must fall between 1 and 65535")

    def _apply(self):
        """Forces immediate runtime propagation across Nginx routes and Firewall rules."""
        try:
            from features.nginx_relay import NginxRelayFeature
            from features.firewall import FirewallFeature
            log.info("Re-applying updated port matrices into system configurations...")
            NginxRelayFeature().install()
            FirewallFeature().install()
        except Exception as exc:
            log.error(f"Failed to synchronize live services with updated ports: {exc}")
