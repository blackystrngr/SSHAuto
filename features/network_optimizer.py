from __future__ import annotations

from pathlib import Path
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature  #

SYSCTL_CONF = Path("/etc/sysctl.d/99-sshauto-optimize.conf")
LIMITS_CONF = Path("/etc/security/limits.d/99-sshauto-limits.conf")
SYSTEMD_SYSTEM_CONF = Path("/etc/systemd/system.conf.d/99-sshauto-limits.conf")

class NetworkOptimizerFeature(BaseFeature):
    name = "network_optimizer"
    description = "Apply ultra low-latency network tweaks and BBR profiling (3x-ui style)"
    depends_on = ["packages"]
    idempotent = True  #

    def is_installed(self) -> bool:
        if not SYSCTL_CONF.exists() or not LIMITS_CONF.exists():
            return False
        bbr_active = Shell.run("sysctl net.ipv4.tcp_congestion_control", capture_output=True, check=False)
        return "bbr" in bbr_active.lower()

    def install(self) -> None:
        log.info("Applying absolute low-latency network profiles...")

        # ------------------------------------------------------------------
        # HIGH-RESPONSE NETWORK TUNING MATRIX
        # ------------------------------------------------------------------
        sysctl_content = """# 3x-ui Core + Extreme Low Latency Profile managed by SSHAuto
# Enable Google BBR for low latency under network stress
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# EXTREME REACTION TIME TWEAKS
# 1. Do not clear congestion window size after connection goes idle (Immediate reaction)
net.ipv4.tcp_slow_start_after_idle=0
# 2. Minimize unsent socket data buffers to explicitly combat bufferbloat
net.ipv4.tcp_notsent_lowat=16384
# 3. Disable automatic packet corking (Flush small packets immediately)
net.ipv4.tcp_autocorking=0
# 4. Instruct kernel to aggressively prioritize processing speed over power/bulk throughput
net.ipv4.tcp_low_latency=1

# Expand incoming request queues to avoid micro-drops
net.core.somaxconn=65535
net.core.netdev_max_backlog=65535
net.ipv4.tcp_max_syn_backlog=65535

# Maximize socket read/write memory windows
net.core.rmem_max=67108864
net.core.wmem_max=67108864
net.ipv4.tcp_rmem=4096 87380 67108864
net.ipv4.tcp_wmem=4096 65536 67108864

# Fast reuse of sockets to prevent port allocation lockouts
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15
net.ipv4.tcp_fastopen=3

# Global descriptor constraints
fs.file-max=2097152
"""
        SYSCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
        SYSCTL_CONF.write_text(sysctl_content)
        
        Shell.run("modprobe tcp_bbr", check=False)
        Shell.run(f"sysctl -p {SYSCTL_CONF}", check=True)
        log.success("Extreme speed network profiles active in the running kernel core.")

        # ------------------------------------------------------------------
        # PROCESS & SYSTEMD MAX DESCRIPTORS
        # ------------------------------------------------------------------
        limits_content = """* soft nofile 1048576
* hard nofile 1048576
root soft nofile 1048576
root hard nofile 1048576
"""
        LIMITS_CONF.parent.mkdir(parents=True, exist_ok=True)
        LIMITS_CONF.write_text(limits_content)

        systemd_content = """[Manager]
DefaultLimitNOFILE=1048576
"""
        SYSTEMD_SYSTEM_CONF.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_SYSTEM_CONF.write_text(systemd_content)
        
        Shell.run("systemctl daemon-reexec", check=False)
        log.success("Concurrency caps scaled successfully.")

    def remove(self) -> None:
        log.info("Restoring stock distribution network values...")
        if SYSCTL_CONF.exists():
            SYSCTL_CONF.unlink()
        if LIMITS_CONF.exists():
            LIMITS_CONF.unlink()
        if SYSTEMD_SYSTEM_CONF.exists():
            SYSTEMD_SYSTEM_CONF.unlink()
            
        Shell.run("sysctl -w net.core.default_qdisc=pfifo_fast", check=False)
        Shell.run("sysctl -w net.ipv4.tcp_congestion_control=cubic", check=False)
        Shell.run("sysctl -w net.ipv4.tcp_slow_start_after_idle=1", check=False)
        Shell.run("systemctl daemon-reexec", check=False)
        log.success("Network profile returned to standard operating defaults.")
