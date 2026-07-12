"""
Every shell-out in this project goes through Shell.run() so we get one
consistent error-handling, retry, timeout, and dry-run policy everywhere
instead of forty slightly different subprocess.run() calls.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass

from core.exceptions import ShellError
from core.logger import log


@dataclass
class CmdResult:
    cmd: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Shell:
    """Thin, defensive wrapper around subprocess with retry/backoff."""

    dry_run: bool = False

    @classmethod
    def run(
        cls,
        cmd: str | list[str],
        *,
        check: bool = True,
        timeout: int = 60,
        retries: int = 0,
        retry_delay: float = 2.0,
        sudo: bool = False,
        input_text: str | None = None,
    ) -> CmdResult:
        cmd_str = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)
        if sudo and not cmd_str.startswith("sudo "):
            cmd_str = f"sudo {cmd_str}"

        if cls.dry_run:
            log.debug(f"[dry-run] {cmd_str}")
            return CmdResult(cmd_str, 0, "", "")

        attempt = 0
        last_error: Exception | None = None
        while attempt <= retries:
            try:
                log.debug(f"$ {cmd_str}")
                proc = subprocess.run(
                    cmd_str,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    input=input_text,
                )
                result = CmdResult(cmd_str, proc.returncode, proc.stdout, proc.stderr)
                if check and not result.ok:
                    raise ShellError(cmd_str, proc.returncode, proc.stderr)
                return result
            except subprocess.TimeoutExpired as exc:
                last_error = ShellError(cmd_str, -1, f"timed out after {timeout}s")
            except ShellError as exc:
                last_error = exc

            attempt += 1
            if attempt <= retries:
                log.warning(f"retry {attempt}/{retries} after failure: {cmd_str}")
                time.sleep(retry_delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def exists(binary: str) -> bool:
        """True if `binary` is on PATH."""
        return shutil.which(binary) is not None

    @staticmethod
    def require(binary: str, package_hint: str | None = None):
        if not Shell.exists(binary):
            from core.exceptions import DependencyError
            hint = f"Install it first (package: {package_hint})." if package_hint else None
            raise DependencyError(f"required binary '{binary}' not found on PATH", hint=hint)
