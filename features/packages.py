"""
First feature to run. Updates apt, installs everything the rest of the
stack needs, and purges packages that would conflict.
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

    def _core_check_list(self) -> list[str]:
        return ["nginx", "dropbear", "openssh-server", "fail2ban",
                "certbot", "python3", "iptables", "git", "squid", "stunnel4", "build-essential", "libpcap-dev", "wget", "dnsmasq"]

    def _dpkg_installed(self, pkg: str) -> bool:
        result = Shell.run(f"dpkg -s {pkg}", check=False)
        return result.ok and "Status: install ok installed" in result.stdout



    def _install_pip_packages(self):
        if not PIP_PACKAGES:
            return
        log.info(f"pip installing: {', '.join(PIP_PACKAGES)}")
        Shell.run(
            "pip3 install --break-system-packages -q " + " ".join(PIP_PACKAGES),
            check=False,
            timeout=180,
        )
