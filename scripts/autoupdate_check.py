#!/usr/bin/env python3
"""
Runs every 30 seconds via systemd timer (sshauto-autoupdate.timer).

Logic:
  1. git fetch quietly
  2. compare local HEAD vs origin/<branch> HEAD
  3. if different: git pull, then re-run `main.py install` (idempotent —
     every feature's install() is safe to call again) so any changed
     package list / template / config takes effect immediately, and
     finally restart the services that matter.

Kept dependency-free and import-light on purpose: this runs unattended,
so we want the smallest possible blast radius if something about the
package layout ever changes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from core.logger import log  # noqa: E402
from core.shell import Shell  # noqa: E402


def current_branch() -> str:
    return Shell.run("git rev-parse --abbrev-ref HEAD", check=False).stdout.strip() or "main"


def main() -> int:
    if not (APP_ROOT / ".git").exists():
        log.debug("not a git checkout, auto-update has nothing to do")
        return 0

    branch = current_branch()
    fetch = Shell.run(f"git fetch origin {branch}", check=False, timeout=30)
    if not fetch.ok:
        log.warning(f"auto-update: git fetch failed: {fetch.stderr.strip()}")
        return 1

    local = Shell.run("git rev-parse HEAD", check=False).stdout.strip()
    remote = Shell.run(f"git rev-parse origin/{branch}", check=False).stdout.strip()

    if not local or not remote or local == remote:
        return 0  # up to date, nothing to do

    log.important(f"new commit detected on {branch}: {local[:7]} -> {remote[:7]}, updating")
    pull = Shell.run(f"git pull --ff-only origin {branch}", check=False, timeout=60)
    if not pull.ok:
        log.error(f"auto-update: git pull failed, staying on {local[:7]}: {pull.stderr.strip()}")
        return 1

    # Re-apply every feature (idempotent ones only — certificates are
    # intentionally excluded so a commit never silently re-issues certs).
    install = Shell.run(
        f"{sys.executable} {APP_ROOT}/main.py install --skip-non-idempotent --quiet",
        check=False,
        timeout=300,
    )
    if install.ok:
        log.success(f"auto-update complete, now at {remote[:7]}")
    else:
        log.error(f"auto-update: re-install after pull had failures: {install.stderr.strip()}")
    return 0 if install.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
