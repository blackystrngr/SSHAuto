"""
features/adblock_dns.py

DNS-level ad / tracker / malware / cryptominer blocking for ALL DNS on the VPS.
dnsmasq listens on port 53, replaces systemd-resolved, and filters every query.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import urllib.request

from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

STATE_DIR = Path("/etc/sshauto/adblock")
BLOCKLIST_FILE = STATE_DIR / "blocklist.hosts"
WHITELIST_FILE = STATE_DIR / "whitelist.txt"
DNSMASQ_CONF = Path("/etc/dnsmasq.d/sshauto-adblock.conf")
STATS_FILE = STATE_DIR / "stats.json"
RESOLV_CONF = Path("/etc/resolv.conf")
RESOLV_CONF_BACKUP = Path("/etc/resolv.conf.sshauto.bak")

BLOCKLIST_SOURCES = [
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "https://urlhaus.abuse.ch/downloads/hostfile/",
    "https://raw.githubusercontent.com/hoshsadiq/adblock-nocoin-list/master/hosts.txt",
]


class AdBlockDNSFeature(BaseFeature):
    name = "adblock_dns"
    description = "Server‑side DNS sinkhole for ads/trackers/malware/cryptominers"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return DNSMASQ_CONF.exists() and BLOCKLIST_FILE.exists()

    def install(self) -> None:
        log.info("Installing server‑side adblock DNS (dnsmasq on port 53)...")

        # 1. Install dnsmasq
        Shell.run("apt-get install -y dnsmasq", check=True)

        # 2. Stop and disable systemd-resolved (it uses port 53)
        Shell.run("systemctl stop systemd-resolved", check=False)
        Shell.run("systemctl disable systemd-resolved", check=False)

        # 3. Backup existing resolv.conf and make it point to localhost
        if RESOLV_CONF.exists() and not RESOLV_CONF_BACKUP.exists():
            Shell.run(f"cp {RESOLV_CONF} {RESOLV_CONF_BACKUP}", check=False)
        RESOLV_CONF.write_text("nameserver 127.0.0.1\n")

        # 4. Create state directories
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        WHITELIST_FILE.touch(exist_ok=True)

        # 5. Fetch blocklists and generate config
        self.refresh_blocklist()
        self._write_dnsmasq_conf()

        # 6. Enable and start dnsmasq
        Shell.run("systemctl enable dnsmasq", check=False)
        Shell.run("systemctl restart dnsmasq", check=False)

        log.success("Server‑side AdBlock DNS installed.")
        log.important("All DNS queries on this VPS (including remote DNS from tunnel clients) are now filtered.")
        log.important("To bypass filtering, add domains to whitelist via dashboard (option 15 -> 3).")

    def remove(self) -> None:
        Shell.run("systemctl stop dnsmasq", check=False)
        Shell.run("systemctl disable dnsmasq", check=False)
        DNSMASQ_CONF.unlink(missing_ok=True)

        # Restore systemd-resolved
        Shell.run("systemctl enable systemd-resolved", check=False)
        Shell.run("systemctl start systemd-resolved", check=False)

        # Restore original resolv.conf
        if RESOLV_CONF_BACKUP.exists():
            Shell.run(f"mv {RESOLV_CONF_BACKUP} {RESOLV_CONF}", check=False)
        else:
            RESOLV_CONF.write_text("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")

        log.info("AdBlock DNS removed – original resolver restored.")

    def refresh_blocklist(self) -> dict:
        log.info("Refreshing blocklists...")
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
            f"""# managed by sshauto adblock_dns -- do not edit by hand
listen-address=127.0.0.1
port=53
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
