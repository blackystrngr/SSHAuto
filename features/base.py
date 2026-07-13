"""
BaseFeature is the contract every plugin (packages, firewall, nginx_relay,
dropbear_service, ssh_service, certificates, fail2ban_service, autoupdate...)
must follow. Adding a new capability to SSHAuto means: drop a new file in
features/, subclass BaseFeature, done — PluginManager auto-discovers it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.logger import log


@dataclass
class FeatureStatus:
    name: str
    installed: bool
    healthy: bool
    detail: str = ""


class BaseFeature:
    name: str = ""
    description: str = ""
    depends_on: list[str] = []
    idempotent: bool = True

    def is_installed(self) -> bool:
        raise NotImplementedError

    def install(self) -> None:
        raise NotImplementedError

    def remove(self) -> None:
        raise NotImplementedError

    # ---- optional overrides -------------------------------------------
    def status(self) -> FeatureStatus:
        try:
            installed = self.is_installed()
        except Exception as exc:  # noqa: BLE001 - status must never crash
            return FeatureStatus(self.name, False, False, detail=str(exc))
        return FeatureStatus(self.name, installed, installed)

    def safe_install(self) -> bool:
        """Wraps install() so one feature's failure doesn't kill the run."""
        try:
            log.rule(self.name)
            log.info(f"{self.description or self.name}")
            self.install()
            log.success(f"{self.name} ready")
            return True
        except Exception as exc:  # noqa: BLE001
            log.error(f"{self.name} failed: {exc}")
            hint = getattr(exc, "hint", None)
            if hint:
                log.warning(hint)
            return False
