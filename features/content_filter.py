from __future__ import annotations

import shutil
from pathlib import Path

from core import blocklists
from core.blocklists import (
    CATEGORIES,
    DESCRIPTIONS,
    DNSFILTER_UNIT,
    DNSMASQ_CONF,
    FILTER_ADDR,
    FILTER_DIR,
    LISTS_DIR,
    RESOLV_CONF,
)
from core.config import SYSTEMD_DIR, state
from core.exceptions import DependencyError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

TUNNEL_UNITS = ("dropbear-tunnel", "ws-ssh-proxy")
UNIT_PATH = SYSTEMD_DIR / f"{DNSFILTER_UNIT}.service"
DROPIN_NAME = "50-sshauto-filter.conf"
PRIVATE_RANGES = ("10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 "
                  "100.64.0.0/10 fc00::/7 fe80::/10")


def render_dnsmasq_conf() -> str:
    return f"""# managed by sshauto
listen-address={FILTER_ADDR}
port=53
bind-interfaces
no-hosts
cache-size=10000
pid-file=/run/{DNSFILTER_UNIT}.pid
# upstream resolvers: the system default (/etc/resolv.conf is read, never modified)
conf-dir={LISTS_DIR},*.conf
"""


def render_unit(dnsmasq_bin: str) -> str:
    return f"""[Unit]
Description=sshauto DNS filter (dnsmasq, loopback only)
After=network.target

[Service]
ExecStart={dnsmasq_bin} -k --conf-file={DNSMASQ_CONF}
Restart=always
RestartSec=2
# small VPS: never let the OOM killer pick the resolver (or the tunnel dies with it)
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
"""


def render_dropin(block_private: bool) -> str:
    text = f"""# managed by sshauto
[Unit]
Wants={DNSFILTER_UNIT}.service
After={DNSFILTER_UNIT}.service

[Service]
# only this service sees the filtered resolver; the system's /etc/resolv.conf is untouched
BindReadOnlyPaths=-{RESOLV_CONF}:/etc/resolv.conf
"""
    if block_private:
        text += f"IPAddressDeny={PRIVATE_RANGES}\n"
    return text


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _unit_active(unit: str) -> bool:
    return Shell.run(f"systemctl is-active {unit}", check=False, timeout=10).ok


class ContentFilterFeature(BaseFeature):
    name = "content_filter"
    description = "DNS filter for tunnel traffic: ads, malware, phishing, cryptominers (optional)"
    depends_on = ["packages", "dropbear_service", "python_proxy"]
    optional = True

    def is_installed(self) -> bool:
        return UNIT_PATH.exists() and _unit_active(DNSFILTER_UNIT)

    def install(self) -> None:
        data = state.ensure_defaults()
        categories = [c for c in data.get("filter_categories", list(CATEGORIES)) if c in CATEGORIES]
        allow = data.get("filter_allow", [])
        block_private = bool(data.get("filter_private", False))

        log.info("Installing dnsmasq (binary only, no system DNS changes)...")
        Shell.run("DEBIAN_FRONTEND=noninteractive apt-get install -y dnsmasq-base",
                  timeout=300, retries=1)
        dnsmasq = shutil.which("dnsmasq") or "/usr/sbin/dnsmasq"
        if not Path(dnsmasq).exists():
            raise DependencyError("dnsmasq binary not found after installing dnsmasq-base")

        FILTER_DIR.mkdir(parents=True, exist_ok=True)
        counts = blocklists.build(categories, allow)
        if categories and not any(counts.values()):
            raise Exception("no blocklist could be downloaded (check outbound access to raw.githubusercontent.com)")

        _write_if_changed(DNSMASQ_CONF, render_dnsmasq_conf())
        _write_if_changed(RESOLV_CONF, f"# managed by sshauto\nnameserver {FILTER_ADDR}\n")
        test = Shell.run(f"{dnsmasq} --test --conf-file={DNSMASQ_CONF}", check=False, timeout=60)
        if not test.ok:
            raise Exception(f"dnsmasq config invalid: {(test.stderr or test.stdout).strip()}")

        _write_if_changed(UNIT_PATH, render_unit(dnsmasq))
        changed = False
        for unit in TUNNEL_UNITS:
            dropin = SYSTEMD_DIR / f"{unit}.service.d" / DROPIN_NAME
            changed |= _write_if_changed(dropin, render_dropin(block_private))
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        Shell.run(f"systemctl enable {DNSFILTER_UNIT}", check=False, timeout=10)
        Shell.run(f"systemctl restart {DNSFILTER_UNIT}", check=False, timeout=30)
        if changed:
            for unit in TUNNEL_UNITS:
                if _unit_active(unit):
                    Shell.run(f"systemctl restart {unit}", check=False, timeout=30)
            log.info("Tunnel services restarted to pick up the filter (clients reconnect).")
        self._verify()
        log.important("Filter active: " + (", ".join(categories) or "no categories")
                      + (" + private networks blocked" if block_private else ""))

    def update(self) -> None:
        """Re-download the lists and reload the resolver."""
        data = state.ensure_defaults()
        categories = [c for c in data.get("filter_categories", list(CATEGORIES)) if c in CATEGORIES]
        blocklists.build(categories, data.get("filter_allow", []))
        Shell.run(f"systemctl restart {DNSFILTER_UNIT}", check=False, timeout=30)
        self._verify()

    def remove(self) -> None:
        Shell.run(f"systemctl disable --now {DNSFILTER_UNIT}", check=False, timeout=20)
        UNIT_PATH.unlink(missing_ok=True)
        for unit in TUNNEL_UNITS:
            dropin_dir = SYSTEMD_DIR / f"{unit}.service.d"
            (dropin_dir / DROPIN_NAME).unlink(missing_ok=True)
            try:
                dropin_dir.rmdir()
            except OSError:
                pass
        shutil.rmtree(FILTER_DIR, ignore_errors=True)
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        for unit in TUNNEL_UNITS:       # drop the resolver bind-mount from the running services
            if _unit_active(unit):
                Shell.run(f"systemctl restart {unit}", check=False, timeout=30)
        log.info("Content filter removed.")

    def summary(self) -> list[str]:
        lines = [f"service {DNSFILTER_UNIT}: {'active' if _unit_active(DNSFILTER_UNIT) else 'inactive'}"]
        for cat in CATEGORIES:
            n = blocklists.count_entries(LISTS_DIR / f"{cat}.conf")
            lines.append(f"{cat:<13} {'on ' if n else 'off'}  {n:>7} domains  ({DESCRIPTIONS[cat]})")
        dropin = SYSTEMD_DIR / "dropbear-tunnel.service.d" / DROPIN_NAME
        private = dropin.exists() and "IPAddressDeny" in dropin.read_text()
        lines.append(f"private networks: {'blocked' if private else 'allowed'}")
        return lines

    def _verify(self) -> None:
        if blocklists.dns_rcode(FILTER_ADDR, "cloudflare.com") is None:
            raise Exception(f"DNS filter does not answer on {FILTER_ADDR}:53 "
                            f"(journalctl -u {DNSFILTER_UNIT})")
        sample = blocklists.sample_blocked_domain()
        if sample and blocklists.dns_rcode(FILTER_ADDR, sample) != 3:
            raise Exception(f"DNS filter is not blocking (expected NXDOMAIN for {sample})")
        log.success("DNS filter answering and blocking.")
