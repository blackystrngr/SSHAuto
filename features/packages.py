"""
First feature to run. Updates apt, installs everything the rest of the
stack needs, and purges packages that would conflict with our setup
(Apache squats on 80/443, ufw/firewalld fight our raw iptables rules).
"""
from __future__ import annotations

from core.config import PIP_PACKAGES, REMOVE_PACKAGES, REQUIRED_PACKAGES
from core.exceptions import PackageError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class PackagesFeature(BaseFeature):
    name = "packages"
    description = "Install required packages, remove conflicting ones"
    depends_on: list[str] = []

    def is_installed(self) -> bool:
        return all(self._dpkg_installed(p) for p in self._core_check_list())

    def install(self) -> None:
        log.info("apt-get update")
        Shell.run("apt-get update -y", timeout=180, retries=2)

        log.info(f"installing {len(REQUIRED_PACKAGES)} packages: "
                  f"{', '.join(REQUIRED_PACKAGES)}")
        try:
            Shell.run(
                "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                + " ".join(REQUIRED_PACKAGES),
                timeout=600,
                retries=1,
            )
        except Exception as exc:
            raise PackageError(f"apt install failed: {exc}") from exc

        self._remove_conflicting()
        self._install_pip_packages()

    def remove(self) -> None:
        log.warning("packages feature does not uninstall required packages "
                     "(too destructive to run automatically); skipping")

    # -- helpers ----------------------------------------------------------
    def _core_check_list(self) -> list[str]:
        return ["nginx", "dropbear", "openssh-server", "fail2ban",
                "certbot", "python3", "iptables", "git", "squid", "stunnel4", "sslh", "haproxy"]

    def _dpkg_installed(self, pkg: str) -> bool:
        result = Shell.run(f"dpkg -s {pkg}", check=False)
        return result.ok and "Status: install ok installed" in result.stdout

    def _remove_conflicting(self):
        present = [p for p in REMOVE_PACKAGES if self._dpkg_installed(p)]
        if not present:
            log.info("no conflicting packages present (apache*/ufw/firewalld)")
            return
        log.important(f"purging conflicting packages: {', '.join(present)}")
        for svc in ("apache2", "ufw", "firewalld"):
            Shell.run(f"systemctl stop {svc}", check=False)
            Shell.run(f"systemctl disable {svc}", check=False)
        Shell.run(
            "DEBIAN_FRONTEND=noninteractive apt-get purge -y " + " ".join(present),
            check=False,
            timeout=180,
        )
        Shell.run("apt-get autoremove -y", check=False, timeout=180)

    def _install_pip_packages(self):
        if not PIP_PACKAGES:
            return
        log.info(f"pip installing: {', '.join(PIP_PACKAGES)}")
        Shell.run(
            "pip3 install --break-system-packages -q " + " ".join(PIP_PACKAGES),
            check=False,
            timeout=180,
        )
