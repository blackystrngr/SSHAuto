"""Blocklist download/normalisation and a tiny DNS probe, shared by the content filter and the doctor."""
from __future__ import annotations

import os
import re
import socket
import time
from pathlib import Path

import requests

from core.logger import log

FILTER_DIR = Path("/etc/sshauto/filter")
LISTS_DIR = FILTER_DIR / "lists"
RESOLV_CONF = FILTER_DIR / "resolv.conf"
DNSMASQ_CONF = FILTER_DIR / "dnsmasq.conf"
DNSFILTER_UNIT = "sshauto-dnsfilter"
FILTER_ADDR = "127.0.0.153"          # loopback alias, no clash with systemd-resolved (127.0.0.53)

_BLP = "https://raw.githubusercontent.com/blocklistproject/Lists/master/alt-version"
_HAGEZI = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq"

CATEGORIES: dict[str, tuple[str, ...]] = {
    "ads": (f"{_BLP}/ads-nl.txt",),
    "malware": (f"{_HAGEZI}/tif.mini.txt",),
    "phishing": (f"{_BLP}/phishing-nl.txt",),
    "cryptominers": (
        f"{_BLP}/crypto-nl.txt",
        "https://raw.githubusercontent.com/hoshsadiq/adblock-nocoin-list/master/hosts.txt",
    ),
}
DESCRIPTIONS = {
    "ads": "ad servers",
    "malware": "malware / C2 (HaGeZi threat feeds, mini)",
    "phishing": "phishing sites",
    "cryptominers": "cryptojacking / mining pools",
}

MAX_BYTES = 40 * 1024 * 1024
MIN_ENTRIES = 100
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_HOST_IPS = ("0.0.0.0", "127.0.0.1", "::", "::1")


def _extract(line: str) -> str | None:
    line = line.split("#", 1)[0].strip()
    if not line or line[0] in "![":
        return None
    if line.startswith(("local=/", "address=/", "server=/")):
        parts = line.split("/")
        return parts[1].lower() if len(parts) > 2 else None
    fields = line.split()
    if len(fields) >= 2 and fields[0] in _HOST_IPS:
        return fields[1].lower().rstrip(".")
    if len(fields) == 1:
        return fields[0].lower().rstrip(".")
    return None


def parse_domains(text: str, allow=()) -> set[str]:
    """Accepts plain domains, hosts-file lines and dnsmasq local=/x/ lines."""
    allow = tuple(a.lower().strip(".") for a in allow if a.strip())
    found: set[str] = set()
    for line in text.splitlines():
        domain = _extract(line)
        if not domain or not _DOMAIN_RE.match(domain):
            continue
        if allow and any(domain == a or domain.endswith("." + a) for a in allow):
            continue
        found.add(domain)
    return found


def download(url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=(10, 60),
                              headers={"User-Agent": "sshauto"}) as resp:
                resp.raise_for_status()
                chunks, size = [], 0
                for chunk in resp.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("list too large")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", "replace")
        except (requests.RequestException, ValueError) as exc:
            log.warning(f"download failed ({attempt}/{retries}) {url}: {exc}")
            time.sleep(2 * attempt)
    return None


def count_entries(path: Path) -> int:
    try:
        with path.open() as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def build(categories, allow=(), lists_dir: Path = LISTS_DIR) -> dict[str, int]:
    """(Re)download the enabled categories into dnsmasq `local=/domain/` files.

    A category whose download fails keeps its previous file. Disabled categories are deleted.
    """
    lists_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for cat, urls in CATEGORIES.items():
        path = lists_dir / f"{cat}.conf"
        if cat not in categories:
            path.unlink(missing_ok=True)
            continue
        domains: set[str] = set()
        for url in urls:
            text = download(url)
            if text:
                domains |= parse_domains(text, allow)
        if len(domains) < MIN_ENTRIES:
            counts[cat] = count_entries(path)
            if counts[cat]:
                log.warning(f"{cat}: update failed, keeping previous list ({counts[cat]} entries)")
            else:
                log.error(f"{cat}: could not download a usable list")
            continue
        tmp = path.with_suffix(".tmp")      # not *.conf, so dnsmasq never reads a half-written file
        tmp.write_text("".join(f"local=/{d}/\n" for d in sorted(domains)))
        tmp.replace(path)
        counts[cat] = len(domains)
        log.success(f"{cat:<13} {len(domains):>7} domains")
    return counts


def sample_blocked_domain(lists_dir: Path = LISTS_DIR) -> str | None:
    for path in sorted(lists_dir.glob("*.conf")):
        with path.open() as fh:
            for line in fh:
                if line.startswith("local=/"):
                    return line.split("/")[1]
    return None


def dns_rcode(server: str, name: str, port: int = 53, timeout: float = 3.0) -> int | None:
    """Return the DNS response code for an A query (0 = ok, 3 = NXDOMAIN), None on no answer."""
    qid = os.urandom(2)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    packet = qid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + qname + b"\x00\x01\x00\x01"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (server, port))
            data, _ = sock.recvfrom(4096)
        except OSError:
            return None
    if len(data) < 4 or data[:2] != qid:
        return None
    return data[3] & 0x0F
