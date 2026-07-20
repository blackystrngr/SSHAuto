"""
Central exception hierarchy for SSHAuto.

Every module raises one of these instead of a bare Exception, so the
top-level CLI can catch SSHAutoError once and print a clean, colored
message instead of a raw Python traceback.
"""


class SSHAutoError(Exception):
    """Base class for every error raised inside SSHAuto."""

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class ShellError(SSHAutoError):
    """A subprocess call returned a non-zero exit code."""

    def __init__(self, cmd: str, returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"command failed ({returncode}): {cmd}\n{self.stderr}",
            hint="Re-run with --verbose to see the full command output.",
        )


class PackageError(SSHAutoError):
    """apt/pip package install or removal failed."""


class ConfigError(SSHAutoError):
    """A config file could not be rendered, read, or validated."""


class ServiceError(SSHAutoError):
    """A systemd service failed to start/reload/restart."""


class CertificateError(SSHAutoError):
    """TLS certificate issuance/detection failed."""


class NetworkError(SSHAutoError):
    """A network operation (git fetch, API call, download) failed."""


class ValidationError(SSHAutoError):
    """User-supplied input (port, domain, username...) failed validation."""


class DependencyError(SSHAutoError):
    """A feature's declared dependency is missing or not installed."""
