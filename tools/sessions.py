#!/usr/bin/env python3
"""Who logged in from where: joins the Dropbear login log with the relay's tunnel audit lines.

Dropbear sees every client as 127.0.0.1:<backend_port>; the relay logs the real client IP for that
same backend_port. Usage (as root):  python3 tools/sessions.py [--since "-2 days"] [--all]
"""
import argparse
import re
import subprocess
from datetime import datetime, timedelta

RELAY_LOG = "/var/log/sshauto/proxy.log"
OPEN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO open kind=(\S+) client=(\S+) relay_port=(\S+) backend_port=(\d+)")
AUTH = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\S* \S+ dropbear\[\d+\]: (Password auth succeeded|Bad password attempt) for '([^']*)' from 127\.0\.0\.1:(\d+)")


def ts(text, fmt):
    return datetime.strptime(text, fmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="-2 days")
    ap.add_argument("--all", action="store_true", help="include failed passwords")
    a = ap.parse_args()

    opens = {}                                  # backend_port -> [(time, kind, client, relay_port)]
    try:
        for line in open(RELAY_LOG, errors="replace"):
            m = OPEN.match(line)
            if m:
                opens.setdefault(m.group(5), []).append((ts(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2), m.group(3), m.group(4)))
    except OSError as exc:
        raise SystemExit(f"cannot read {RELAY_LOG}: {exc}")

    out = subprocess.run(["journalctl", "-u", "dropbear-tunnel", "-o", "short-iso", "--no-pager", "--since", a.since],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        m = AUTH.match(line)
        if not m or (not a.all and m.group(2) != "Password auth succeeded"):
            continue
        when, what, user, port = ts(m.group(1), "%Y-%m-%dT%H:%M:%S"), m.group(2), m.group(3), m.group(4)
        # backend ports are reused: take the latest tunnel opened on that port shortly before the login
        cands = [o for o in opens.get(port, []) if timedelta(0) <= when - o[0] <= timedelta(minutes=10)]
        o = max(cands, key=lambda x: x[0]) if cands else None
        rows.append((when, "ok" if what.startswith("Password auth") else "FAIL", user,
                     o[2] if o else "unknown (no relay audit line)", o[1] if o else "-", o[3] if o else "-"))

    print(f"{'time':19}  {'res':4}  {'account':18}  {'client ip':28}  kind  port")
    for r in rows:
        print(f"{r[0]:%Y-%m-%d %H:%M:%S}  {r[1]:4}  {r[2]:18}  {r[3]:28}  {r[4]:5} {r[5]}")
    if not rows:
        print("no matching logins (the relay audit lines only exist after the updated relay is running)")


if __name__ == "__main__":
    main()
