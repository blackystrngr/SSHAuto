from __future__ import annotations

import importlib
import pkgutil
import time

import features as features_pkg
from core.exceptions import DependencyError
from core.logger import log
from features.base import BaseFeature


class PluginManager:
    def __init__(self):
        self._classes = {}
        self._discover()
    def _discover(self):
        for _, module_name, _ in pkgutil.iter_modules(features_pkg.__path__):
            if module_name in ("base",):
                continue
            module = importlib.import_module(f"features.{module_name}")
            for attr in vars(module).values():
                if isinstance(attr, type) and issubclass(attr, BaseFeature) and attr is not BaseFeature:
                    self._classes[attr.name] = attr
    def names(self): return sorted(self._classes)
    def default_names(self): return sorted(n for n, c in self._classes.items() if not c.optional)
    def get(self, name): return self._classes[name]()
    def _ordered(self, wanted=None):
        wanted = wanted or self.names()
        resolved = []
        visiting = set()
        def visit(n):
            if n in resolved: return
            if n in visiting: raise DependencyError(f"circular dependency at '{n}'")
            if n not in self._classes: raise DependencyError(f"'{n}' depends on unknown feature")
            visiting.add(n)
            for dep in self._classes[n].depends_on:
                visit(dep)
            visiting.discard(n)
            resolved.append(n)
        for n in sorted(wanted, key=lambda n: (self._classes[n].priority if n in self._classes else 50, n)):
            visit(n)
        return [self.get(n) for n in resolved]
    def install_all(self, only=None, force=False, include=()):
        results = {}
        wanted = list(only) if only else self.default_names()
        wanted += [n for n in include if n not in wanted]
        failed: set[str] = set()
        for feature in self._ordered(wanted):
            broken = [d for d in feature.depends_on if d in failed]
            if broken:
                log.error(f"{feature.name} skipped: dependency failed ({', '.join(broken)})")
                results[feature.name] = False
                failed.add(feature.name)
                continue
            if not force and feature.is_installed():
                log.info(f"{feature.name} already installed, skipping (use --force to overwrite)")
                continue
            ok = feature.safe_install()
            if not ok and feature.idempotent:
                log.warning(f"retrying {feature.name} once...")
                time.sleep(3)
                ok = feature.safe_install()
            results[feature.name] = ok
            if not ok:
                failed.add(feature.name)
        ok = sum(results.values())
        log.rule("summary")
        log.info(f"{ok}/{len(results)} features installed successfully")
        failed = [n for n, s in results.items() if not s]
        if failed:
            log.warning(f"failed: {', '.join(failed)}")
        return results
    def status_all(self):
        for feature in self._ordered():
            s = feature.status()
            if not s.installed and feature.optional:
                log.info(f"{s.name:<18} optional, not installed")
            elif s.installed:
                log.success(f"{s.name:<18} installed")
            else:
                log.warning(f"{s.name:<18} not installed  {s.detail}")
