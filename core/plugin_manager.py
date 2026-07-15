"""
PluginManager – discovers and manages features.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Type

import features as features_pkg
from core.exceptions import DependencyError
from core.logger import log
from features.base import BaseFeature


class PluginManager:
    def __init__(self):
        self._classes: dict[str, Type[BaseFeature]] = {}
        self._discover()

    def _discover(self):
        for _, module_name, _ in pkgutil.iter_modules(features_pkg.__path__):
            if module_name in ("base",):
                continue
            module = importlib.import_module(f"features.{module_name}")
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseFeature)
                    and attr is not BaseFeature
                ):
                    self._classes[attr.name] = attr

    def names(self) -> list[str]:
        return sorted(self._classes)

    def get(self, name: str) -> BaseFeature:
        if name not in self._classes:
            raise DependencyError(f"unknown feature '{name}'")
        return self._classes[name]()

    def _ordered(self, wanted: list[str] | None = None) -> list[BaseFeature]:
        wanted = wanted or self.names()
        resolved: list[str] = []
        visiting: set[str] = set()

        def visit(n: str):
            if n in resolved:
                return
            if n in visiting:
                raise DependencyError(f"circular dependency detected at '{n}'")
            if n not in self._classes:
                raise DependencyError(f"'{n}' depends on unknown feature")
            visiting.add(n)
            for dep in self._classes[n].depends_on:
                visit(dep)
            visiting.discard(n)
            resolved.append(n)

        for n in wanted:
            visit(n)
        return [self.get(n) for n in resolved]

    def install_all(self, only: list[str] | None = None, force: bool = False) -> dict[str, bool]:
        results = {}
        for feature in self._ordered(only):
            if not force and feature.is_installed():
                log.info(f"{feature.name} already installed, skipping (use --force to overwrite)")
                continue
            results[feature.name] = feature.safe_install()
        ok = sum(results.values())
        log.rule("summary")
        log.info(f"{ok}/{len(results)} features installed successfully")
        failed = [n for n, success in results.items() if not success]
        if failed:
            log.warning(f"failed: {', '.join(failed)} (see log above)")
        return results

    def status_all(self):
        for feature in self._ordered():
            s = feature.status()
            if s.installed:
                log.success(f"{s.name:<18} installed")
            else:
                log.warning(f"{s.name:<18} not installed  {s.detail}")
