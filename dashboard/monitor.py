"""
Everything the dashboard's "live" screen needs: how many tunnels are
currently open, how many accounts exist in total, current network
throughput (from kernel counters — instant, no external calls), and an
on-demand internet speed test (one external download, only when asked).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.config import SSH_PORT_DEFAULT, state
from core.shell import Shell
from dashboard.users import UserManager

SPEEDTEST_URL = "https://speed.cloudflare.com/__down?bytes=25000000"


@dataclass
class LiveStats:
    active_connections: int
    total_users: int
    rx_kbps: float
    tx_kbps: float


class Monitor:
    def __init__(self):
        self.users = UserManager()

    def live_stats(self, sample_seconds: float = 1.0) -> LiveStats:
        return LiveStats(
            active_connections=self._active_connections(),
            total_users=len(self.users.list()),
            **self._throughput(sample_seconds),
        )

    def _active_connections(self) -> int:
        data = state.ensure_defaults()
        ssh_port = data.get("ssh_port", SSH_PORT_DEFAULT)
        dropbear_port = data.get("dropbear_port")
        ports = {p for p in (ssh_port, dropbear_port) if p}
        if not ports:
            return 0
        filter_expr = " or ".join(f"dport = :{p} or sport = :{p}" for p in ports)
        result = Shell.run(f"ss -tn state established '( {filter_expr} )'", check=False)
        if not result.ok:
            return 0
        # first line is the header
        lines = [l for l in result.stdout.splitlines()[1:] if l.strip()]
        return len(lines)

    def _throughput(self, sample_seconds: float) -> dict:
        iface = self._default_iface()
        if not iface:
            return {"rx_kbps": 0.0, "tx_kbps": 0.0}

        rx1, tx1 = self._read_iface_bytes(iface)
        time.sleep(sample_seconds)
        rx2, tx2 = self._read_iface_bytes(iface)

        rx_kbps = ((rx2 - rx1) * 8 / 1024) / sample_seconds
        tx_kbps = ((tx2 - tx1) * 8 / 1024) / sample_seconds
        return {"rx_kbps": round(rx_kbps, 1), "tx_kbps": round(tx_kbps, 1)}

    def _default_iface(self) -> str | None:
        result = Shell.run("ip route show default", check=False)
        if not result.ok:
            return None
        parts = result.stdout.split()
        return parts[parts.index("dev") + 1] if "dev" in parts else None

    def _read_iface_bytes(self, iface: str) -> tuple[int, int]:
        try:
            with open(f"/sys/class/net/{iface}/statistics/rx_bytes") as f:
                rx = int(f.read().strip())
            with open(f"/sys/class/net/{iface}/statistics/tx_bytes") as f:
                tx = int(f.read().strip())
            return rx, tx
        except OSError:
            return 0, 0

    def speed_test(self) -> float | None:
        """One-shot download speed test in Mbps. Returns None on failure."""
        result = Shell.run(
            f'curl -o /dev/null -s -w "%{{speed_download}}" --max-time 10 "{SPEEDTEST_URL}"',
            check=False,
            timeout=15,
        )
        if not result.ok or not result.stdout.strip():
            return None
        try:
            bytes_per_sec = float(result.stdout.strip())
        except ValueError:
            return None
        return round(bytes_per_sec * 8 / 1_000_000, 2)  # Mbps
