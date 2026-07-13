from __future__ import annotations

from pathlib import Path
from core.shell import Shell
from core.logger import log
from features.base import BaseFeature

SYSCTL_CONF_PATH = Path("/etc/sysctl.d/99-sshauto-optimizer.conf")

class NetworkOptimizerFeature(BaseFeature):
    name = "network_optimizer"
    description = "Optimize routing latency & enable BBR congestion control layers"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        # no capture_output – Shell.run returns an object with .ok and .stdout
        bbr_active = Shell.run("sysctl net.ipv4.tcp_congestion_control", check=False)
        if not bbr_active.ok:
            return False
        return SYSCTL_CONF_PATH.exists() and "bbr" in bbr_active.stdout.lower()

    def install(self) -> None:
        log.info("Applying kernel network optimization variables...")
        tweaks = [
            "# Auto-generated optimization parameters by SSHAuto",
            "net.core.default_qdisc=fq",
            "net.ipv4.tcp_congestion_control=bbr",
            "net.ipv4.tcp_fastopen=3",
            "net.ipv4.tcp_rmem=4096 87380 16777216",
            "net.ipv4.tcp_wmem=4096 65536 16777216",
            "net.core.rmem_max=16777216",
            "net.core.wmem_max=16777216",
            "net.ipv4.tcp_max_syn_backlog=4096",
            "net.ipv4.tcp_tw_reuse=1",
        ]
        SYSCTL_CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
        SYSCTL_CONF_PATH.write_text("\n".join(tweaks) + "\n")

        reload_res = Shell.run("sysctl --system", check=False)
        if not reload_res.ok:
            log.warning("Some sysctl parameters could not be reloaded instantly. A reboot might be required.")
        log.success("Network optimizations and BBR layers deployed successfully.")

    def remove(self) -> None:
        log.info("Reverting network optimization matrix configurations...")
        if SYSCTL_CONF_PATH.exists():
            SYSCTL_CONF_PATH.unlink()
        Shell.run("sysctl -w net.ipv4.tcp_congestion_control=cubic", check=False)
        Shell.run("sysctl --system", check=False)
        log.success("Network profile optimization values cleaned.")
