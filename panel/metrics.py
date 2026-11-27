"""System metrics from /proc, sampled in the background into a ring buffer."""
from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from collections import deque

from core.config import SSH_PORT_DEFAULT, state
from core.shell import Shell

SAMPLE_SECONDS = 3
HISTORY = 400                      # ~20 minutes


def parse_cpu(stat_text: str) -> tuple[int, int]:
    """(busy, total) jiffies from the first line of /proc/stat."""
    fields = [int(x) for x in stat_text.splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields[:8])
    return total - idle, total


def parse_meminfo(text: str) -> tuple[int, int]:
    """(used, total) bytes."""
    kv = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            kv[key] = int(parts[0]) * 1024
    total = kv.get("MemTotal", 0)
    return max(total - kv.get("MemAvailable", kv.get("MemFree", 0)), 0), total


def parse_netdev(text: str, iface: str) -> tuple[int, int] | None:
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name.strip() == iface:
            cols = rest.split()
            return int(cols[0]), int(cols[8])
    return None


def default_iface(route_text: str) -> str | None:
    for line in route_text.splitlines()[1:]:
        cols = line.split()
        if len(cols) > 1 and cols[1] == "00000000":
            return cols[0]
    return None


def _read(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def count_tunnels() -> int:
    data = state.load()
    ports = {p for p in (data.get("ssh_port", SSH_PORT_DEFAULT), data.get("dropbear_port")) if p}
    if not ports:
        return 0
    expr = " or ".join(f"sport = :{p}" for p in ports)
    res = Shell.run(f"ss -tn state established '( {expr} )'", check=False, timeout=5)
    return len([ln for ln in res.stdout.splitlines()[1:] if ln.strip()]) if res.ok else 0


class Sampler:
    def __init__(self):
        self.history: deque = deque(maxlen=HISTORY)
        self._lock = threading.Lock()
        self._iface = default_iface(_read("/proc/net/route"))
        self._prev_cpu = parse_cpu(_read("/proc/stat")) if _read("/proc/stat") else (0, 0)
        self._prev_net = (parse_netdev(_read("/proc/net/dev"), self._iface) if self._iface else None)
        self._prev_t = time.monotonic()
        self._thread = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="metrics")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:
                pass
            time.sleep(SAMPLE_SECONDS)

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(now - self._prev_t, 0.001)
        stat_text = _read("/proc/stat")
        busy, total = parse_cpu(stat_text) if stat_text else (0, 0)
        pb, pt = self._prev_cpu
        cpu = round(100 * (busy - pb) / (total - pt), 1) if total > pt else 0.0
        rx = tx = 0.0
        net = parse_netdev(_read("/proc/net/dev"), self._iface) if self._iface else None
        if net and self._prev_net:
            rx = max(net[0] - self._prev_net[0], 0) * 8 / 1000 / dt       # kbit/s
            tx = max(net[1] - self._prev_net[1], 0) * 8 / 1000 / dt
        used, mem_total = parse_meminfo(_read("/proc/meminfo"))
        point = {
            "t": int(time.time()), "cpu": cpu, "rx": round(rx, 1), "tx": round(tx, 1),
            "mem": round(100 * used / mem_total, 1) if mem_total else 0.0,
            "tunnels": count_tunnels(),
        }
        self._prev_cpu, self._prev_net, self._prev_t = (busy, total), net, now
        with self._lock:
            self.history.append(point)
        return point

    def series(self, points: int = 120) -> list[dict]:
        with self._lock:
            data = list(self.history)
        if len(data) <= points:
            return data
        step = len(data) / points
        return [data[int(i * step)] for i in range(points)] + [data[-1]]

    def latest(self) -> dict:
        with self._lock:
            return dict(self.history[-1]) if self.history else {}


def system_info() -> dict:
    used, total = parse_meminfo(_read("/proc/meminfo"))
    disk = shutil.disk_usage("/")
    try:
        load = os.getloadavg()
    except OSError:
        load = (0.0, 0.0, 0.0)
    up = _read("/proc/uptime").split()
    return {
        "hostname": socket.gethostname(),
        "cores": os.cpu_count() or 1,
        "uptime": int(float(up[0])) if up else 0,
        "load": [round(x, 2) for x in load],
        "mem_used": used, "mem_total": total,
        "disk_used": disk.used, "disk_total": disk.total,
    }
