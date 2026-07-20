"""
BaseFeature is the contract every plugin must follow.
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


class BaseFeature(ABC):
    name: str = "unnamed"
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    idempotent: bool = True

    def __repr__(self):
        return f"<Feature {self.name}>"

    @abstractmethod
    def is_installed(self) -> bool:
        pass

    @abstractmethod
    def install(self) -> None:
        pass

    @abstractmethod
    def remove(self) -> None:
        pass

    def status(self) -> FeatureStatus:
        try:
            installed = self.is_installed()
        except Exception as exc:
            return FeatureStatus(self.name, False, False, detail=str(exc))
        return FeatureStatus(self.name, installed, installed)

    def safe_install(self) -> bool:
        try:
            log.rule(self.name)
            log.info(f"{self.description or self.name}")
            self.install()
            log.success(f"{self.name} ready")
            return True
        except Exception as exc:
            log.error(f"{self.name} failed: {exc}")
            hint = getattr(exc, "hint", None)
            if hint:
                log.warning(hint)
            return False
