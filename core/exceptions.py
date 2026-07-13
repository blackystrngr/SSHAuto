"""
Central exception hierarchy for SSHAuto.
"""


class SSHAutoError(Exception):
    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class ShellError(SSHAutoError):
    def __init__(self, cmd: str, returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"command failed ({returncode}): {cmd}\n{self.stderr}",
            hint="Re-run with --verbose to see the full command output.",
        )


class PackageError(SSHAutoError):
    pass


class ConfigError(SSHAutoError):
    pass


class ServiceError(SSHAutoError):
    pass


class CertificateError(SSHAutoError):
    pass


class NetworkError(SSHAutoError):
    pass


class ValidationError(SSHAutoError):
    pass


class DependencyError(SSHAutoError):
    pass
