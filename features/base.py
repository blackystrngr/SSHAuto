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

    @abstractmethod
    def is_installed(self) -> bool: ...
    @abstractmethod
    def install(self) -> None: ...
    @abstractmethod
    def remove(self) -> None: ...

    def status(self) -> FeatureStatus:
        try:
            installed = self.is_installed()
        except Exception as exc:
            return FeatureStatus(self.name, False, False, detail=str(exc))
        return FeatureStatus(self.name, installed, installed)

    def safe_install(self) -> bool:
        try:
            log.rule(self.name)
            log.info(self.description or self.name)
            self.install()
            log.success(f"{self.name} ready")
            return True
        except Exception as exc:
            log.error(f"{self.name} failed: {exc}")
            if hasattr(exc, "hint") and exc.hint:
                log.warning(exc.hint)
            return False
