"""
features/adblock_dns.py

DNS-level ad / tracker / malware / cryptominer blocking for tunnel clients.
Clients that route DNS through the tunnel get filtered by a local dnsmasq instance.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import urllib.request

from core.config import state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

STATE_DIR = Path("/etc/sshauto/adblock")
BLOCKLIST_FILE = STATE_DIR / "blocklist.hosts"
WHITELIST_FILE = STATE_DIR / "whitelist.txt"
DNSMASQ_CONF = Path("/etc/dnsmasq.d/sshauto-adblock.conf")
STATS_FILE = STATE_DIR / "stats.json"

BLOCKLIST_SOURCES = [
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "https://urlhaus.abuse.ch/downloads/hostfile/",
    "https://raw.githubusercontent.com/hoshsadiq/adblock-nocoin-list/master/hosts.txt",
]

DNSMASQ_LISTEN_PORT = 5353  # avoid systemd-resolved on 53


class AdBlockDNSFeature(BaseFeature):
    name = "adblock_dns"
    description = "DNS sinkhole for ads/trackers/malware/cryptominers (dnsmasq)"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return DNSMASQ_CONF.exists() and BLOCKLIST_FILE.exists()

    def install(self) -> None:
        log.info("Installing adblock_dns feature (dnsmasq)...")

        Shell.run("apt-get install -y dnsmasq", check=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        WHITELIST_FILE.touch(exist_ok=True)

        self.refresh_blocklist()
        self._write_dnsmasq_conf()
        Shell.run("systemctl enable --now dnsmasq", check=False)
        Shell.run("systemctl restart dnsmasq", check=False)

        log.success("AdBlock DNS installed.")
        log.important(
            f"Point tunnel clients' remote DNS at this VPS on port {DNSMASQ_LISTEN_PORT} "
            "to enable filtering (e.g. SOCKS proxy with 'proxy DNS' / remote DNS)."
        )

    def remove(self) -> None:
        Shell.run("systemctl disable --now dnsmasq", check=False)
        DNSMASQ_CONF.unlink(missing_ok=True)
        Shell.run("systemctl restart dnsmasq", check=False)
        log.info("AdBlock DNS removed.")

    def refresh_blocklist(self) -> dict:
        log.info("Refreshing adblock blocklists...")
        domains: set[str] = set()
        per_source_counts = {}

        for url in BLOCKLIST_SOURCES:
            try:
                raw = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", errors="ignore")
            except Exception as e:
                log.warning(f"blocklist fetch failed: {url} ({e})")
                continue

            before = len(domains)
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^(?:0\.0\.0\.0|127\.0\.0\.1)\s+(\S+)", line)
                domain = match.group(1) if match else line.split()[0]
                if domain and "." in domain:
                    domains.add(domain.lower())
            per_source_counts[url] = len(domains) - before

        whitelist = {
            d.strip().lower()
            for d in WHITELIST_FILE.read_text().splitlines()
            if d.strip() and not d.startswith("#")
        }
        domains -= whitelist

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with BLOCKLIST_FILE.open("w") as f:
            f.writelines(f"0.0.0.0 {d}\n" for d in sorted(domains))

        stats = {
            "total_blocked": len(domains),
            "whitelisted": len(whitelist),
            "per_source": per_source_counts,
        }
        STATS_FILE.write_text(json.dumps(stats, indent=2))

        Shell.run("systemctl reload dnsmasq", check=False)
        log.success(f"Blocklist refreshed – {stats['total_blocked']} domains blocked.")
        return stats

    def whitelist_add(self, domain: str) -> None:
        with WHITELIST_FILE.open("a") as f:
            f.write(domain.strip().lower() + "\n")
        self.refresh_blocklist()

    def whitelist_remove(self, domain: str) -> None:
        domain = domain.strip().lower()
        lines = [d for d in WHITELIST_FILE.read_text().splitlines() if d.strip() != domain]
        WHITELIST_FILE.write_text("\n".join(lines) + "\n")
        self.refresh_blocklist()

    def stats(self) -> dict:
        if STATS_FILE.exists():
            return json.loads(STATS_FILE.read_text())
        return {"total_blocked": 0, "whitelisted": 0, "per_source": {}}

    def _write_dnsmasq_conf(self) -> None:
        DNSMASQ_CONF.parent.mkdir(parents=True, exist_ok=True)
        DNSMASQ_CONF.write_text(
            f"""# managed by sshauto adblock_dns feature -- do not edit by hand
listen-address=127.0.0.1
port={DNSMASQ_LISTEN_PORT}
bind-interfaces
addn-hosts={BLOCKLIST_FILE}
cache-size=10000
no-resolv
server=1.1.1.1
server=9.9.9.9
log-queries=extra
log-facility=/var/log/sshauto/adblock_dns.log
"""
        )
