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
        super().__init__(f"command failed ({returncode}): {cmd}\n{self.stderr}")

class PackageError(SSHAutoError): pass
class ConfigError(SSHAutoError): pass
class ServiceError(SSHAutoError): pass
class CertificateError(SSHAutoError): pass
class ValidationError(SSHAutoError): pass
class DependencyError(SSHAutoError): pass
