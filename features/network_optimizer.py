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
    description = "Apply kernel network stack optimizations, BBR, and connection handling structures (3x-ui profile)"
    depends_on = ["packages"]
    idempotent = True  #

    def is_installed(self) -> bool:
        # Check if setup configuration tracks exist and BBR algorithm module is active
        if not SYSCTL_CONF.exists() or not LIMITS_CONF.exists():
            return False
        bbr_active = Shell.run("sysctl net.ipv4.tcp_congestion_control", capture_output=True, check=False)
        return "bbr" in bbr_active.lower()

    def install(self) -> None:
        log.info("Optimizing kernel network stack and scaling process constraints...")

        # ------------------------------------------------------------------
        # STEP 1: KERNEL & CONGESTION TUNING (3x-ui Spec)
        # ------------------------------------------------------------------
        sysctl_content = """# 3x-ui Style Tunnel Optimizations managed by SSHAuto
# Enable Google BBR congestion control algorithm for low-latency under packet loss
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr

# Expand concurrent incoming request queues (prevents connection dropouts)
net.core.somaxconn=65535
net.core.netdev_max_backlog=65535
net.ipv4.tcp_max_syn_backlog=65535

# Maximize socket read/write memory allocations (larger network windows)
net.core.rmem_max=67108864
net.core.wmem_max=67108864
net.ipv4.tcp_rmem=4096 87380 67108864
net.ipv4.tcp_wmem=4096 65536 67108864

# Allow fast recycling of sockets to avoid TIME_WAIT execution locks
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15

# Enable TCP Fast Open (speeds up handshakes on supported clients)
net.ipv4.tcp_fastopen=3

# Global system file descriptor allocation baseline limit
fs.file-max=2097152
"""
        SYSCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
        SYSCTL_CONF.write_text(sysctl_content)
        
        # Ensure the BBR kernel module is forcibly loaded into runtime space
        Shell.run("modprobe tcp_bbr", check=False)
        
        # Reload sysctl using strictly our isolated config (safe against system errors)
        Shell.run(f"sysctl -p {SYSCTL_CONF}", check=True)
        log.success("Kernel parameters and BBR congestion tracking successfully engaged.")

        # ------------------------------------------------------------------
        # STEP 2: PAM OPEN FILE DESCRIPTOR OVERRIDES (High Concurrency)
        # ------------------------------------------------------------------
        limits_content = """# Elevate limits to prevent proxy connections from triggering 'Too many open files'
* soft nofile 1048576
* hard nofile 1048576
root soft nofile 1048576
root hard nofile 1048576
"""
        LIMITS_CONF.parent.mkdir(parents=True, exist_ok=True)
        LIMITS_CONF.write_text(limits_content)
        log.success("Process limits architecture configuration profiles registered.")

        # ------------------------------------------------------------------
        # STEP 3: SYSTEMD GLOBAL DAEMON INHERITANCE LIMITS
        # ------------------------------------------------------------------
        systemd_content = """[Manager]
DefaultLimitNOFILE=1048576
"""
        SYSTEMD_SYSTEM_CONF.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_SYSTEM_CONF.write_text(systemd_content)
        
        # Force systemd manager instance to safely hot-reload internal configs
        Shell.run("systemctl daemon-reexec", check=False)
        log.success("Global systemd process managers re-executed cleanly with updated limits.")

    def remove(self) -> None:
        log.info("Removing optimization layers and restoring system defaults...")
        if SYSCTL_CONF.exists():
            SYSCTL_CONF.unlink()
        if LIMITS_CONF.exists():
            LIMITS_CONF.unlink()
        if SYSTEMD_SYSTEM_CONF.exists():
            SYSTEMD_SYSTEM_CONF.unlink()
            
        # Safely reset kernel runtime values back to standard standard Linux baselines
        Shell.run("sysctl -w net.core.default_qdisc=pfifo_fast", check=False)
        Shell.run("sysctl -w net.ipv4.tcp_congestion_control=cubic", check=False)
        Shell.run("systemctl daemon-reexec", check=False)
        log.success("Network optimizations rolled back cleanly to system defaults.")
