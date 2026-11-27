# sshauto

[![CI](https://github.com/blackystrngr/SSHAuto/actions/workflows/ci.yml/badge.svg)](https://github.com/blackystrngr/SSHAuto/actions/workflows/ci.yml)

SSH-over-WebSocket relay autoscript for Debian/Ubuntu VPS. One command installs and
wires together a Python WebSocket proxy, Dropbear, OpenSSH, fail2ban, iptables
rules and TLS certificates, then gives you a `kk` dashboard to manage users and ports.

> **Use responsibly.** Run this only on infrastructure you own, in line with your
> upstream providers' terms of service and local law.

## Architecture

```
client ─▶ CDN (Cloudflare, etc.) ─▶ Python proxy, bound directly on every HTTP/HTTPS
                                     relay port (80, 443, 8080, 2083, ...) — no nginx
                                        │
                                        └─▶ Dropbear (127.0.0.1:110)
```

There is **no reverse proxy in front of the Python relay by default.** It binds
directly, on `0.0.0.0`, to every plain HTTP relay port (`80, 8080, 8880, 2052,
2082, 2086, 2095` plus any custom ones) and, terminating TLS itself with the
stored certificate, every HTTPS relay port (`443, 8443, 2053, 2083, 2087, 2096`
plus custom). This is what lets it accept a request carrying **two `Host:`
headers**: nginx's HTTP parser rejects those outright (`400`), but this proxy's
own line-by-line parser keeps the first `Host` and quietly ignores any later
copies, so a dual-Host WebSocket payload (plain or TLS) still reaches the
upgrade logic and gets tunneled to Dropbear. A plain (non-upgrade) HTTP request
is forwarded to whatever its first `Host:` names, with every header line —
duplicates included — passed through untouched.

| Component | Role |
|---|---|
| Python proxy | uvloop-based. Binds directly on every HTTP/HTTPS relay port (public), handles WebSocket upgrade, HTTP `CONNECT` and plain HTTP forwarding, dual-Host tolerant, terminates TLS itself (systemd: `ws-ssh-proxy`) |
| Dropbear | Tunnel SSH backend, loopback only (systemd: `dropbear-tunnel`) |
| OpenSSH | Direct admin access on its own port, with a random banner |
| fail2ban | Jails for sshd and Dropbear |
| iptables | Rules are added, never flushed |
| Certificates | Cloudflare Origin Certificate (15 years) or self-signed, chosen from an interactive menu |
| udpgw | Optional UDP forwarding via badvpn-udpgw |
| Squid | Optional authenticated proxy, see below |
| Content filter | Optional DNS blocking of ads, malware, phishing and cryptominers for tunnel traffic, see below |
| nginx (`nginx_relay`) | **Optional, off by default.** Classic reverse-proxy mode: nginx becomes the public listener and the Python proxy drops back to loopback-only. Dual-Host stops working in this mode (that limitation is nginx's). Only turn this on if you need nginx for something else (e.g. the web panel) |

## Install

On the target VPS, as root:

```bash
bash <(curl -LS https://raw.githubusercontent.com/blackystrngr/SSHAuto/main/install.sh)
```

Or from a clone:

```bash
sudo python3 main.py install
```

You will be asked for a domain and a certificate type. The installer then verifies the
full tunnel path (nginx → proxy → Dropbear) and repairs what it can. When it finishes, type `kk`.

## Commands

```bash
sudo python3 main.py install [--only a,b] [--force] [--quiet] [--skip-non-idempotent]
sudo python3 main.py install --squid     # also install the optional Squid proxy
sudo python3 main.py squid               # install/reconfigure Squid on its own
sudo python3 main.py install --panel     # also install the optional web panel
sudo python3 main.py panel               # install/manage the web panel (--status, --reset-password, --remove)
sudo python3 main.py doctor              # verify tunnel end-to-end and auto-repair (--no-heal: check only)
sudo python3 main.py status              # per-feature install status
sudo python3 main.py cert                # re-run the certificate menu
sudo python3 main.py uninstall           # remove everything sshauto built (asks first)
sudo python3 main.py uninstall --yes --keep-users --purge-packages   # flags: skip prompt / keep accounts / apt-purge nginx, dropbear, fail2ban, squid
sudo python3 main.py dashboard           # same as `kk`
python3 main.py --version
```

## Optional Squid proxy

Not installed by default. Authentication is always required.

| Setting | Default |
|---|---|
| Username | `didntgo` |
| Password | `didntgo1#` |
| Port | `3128` |

```bash
sudo python3 main.py squid --squid-user bob --squid-pass 's3cret' --squid-port 3128
sudo python3 main.py squid --remove
```

Change the default password before exposing the server. Credentials are stored in
`/var/lib/sshauto/state.json` (mode 600).

## Optional content filter

Blocks ad, malware, phishing and cryptominer domains for everything that goes through the
tunnel (SSH-over-WebSocket and the `CONNECT` proxy). Not installed by default.

```bash
sudo python3 main.py install --filter                  # with the rest, all four categories
sudo python3 main.py filter                            # add/reconfigure on its own
sudo python3 main.py filter --categories ads,phishing  # ads,malware,phishing,cryptominers | all | none
sudo python3 main.py filter --allow example.com,b.org  # never block these (and their subdomains)
sudo python3 main.py filter --block-private            # also block private/link-local networks
sudo python3 main.py filter --status
sudo python3 main.py filter --update                   # re-download the lists (manual, no timers)
sudo python3 main.py filter --remove
```

| Category | Source | Entries (approx.) |
|---|---|---|
| ads | Block List Project `ads` | 234k |
| malware | HaGeZi threat intelligence feeds (mini) | 164k |
| phishing | Block List Project `phishing` | 190k |
| cryptominers | Block List Project `crypto` + NoCoin | 1.5k |

How it works: a loopback-only `dnsmasq` (`sshauto-dnsfilter`, `127.0.0.153`) answers NXDOMAIN for listed
domains and their subdomains, and forwards everything else to the system's own resolvers. It is mounted over
`/etc/resolv.conf` **only for `dropbear-tunnel` and `ws-ssh-proxy`** (systemd drop-ins), so the system DNS is
never modified. With all categories enabled `dnsmasq` used about 53 MB RAM (measured, 589k domains); disable
categories on a very small VPS.

`--block-private` adds `IPAddressDeny` for RFC1918, link-local (cloud metadata at `169.254.169.254`),
CGNAT and IPv6 ULA to the same two services, so tunnel users cannot reach your private network. Loopback is
not blocked because the relay itself uses it.

Limits: this is DNS filtering. A client that connects to an IP address directly, or uses its own DNS or
DNS-over-HTTPS, is not filtered. The optional Squid proxy is not covered.

## Optional web panel

A browser UI to monitor and manage the server: live tunnel path, throughput chart, CPU/memory/disk,
users (create, extend, lock, delete), relay ports, Squid, content filter, service restarts, health
check with one-click repair, speed test, logs. Opt in with `install --panel` or `main.py panel`.

- The panel server listens on `127.0.0.1` only. nginx publishes it under a secret URL path on the
  existing relay ports; the URL and a generated admin password are printed once at install.
  Lost password: `sudo python3 main.py panel --reset-password`.
- Sign-in: scrypt password hash, signed HttpOnly/SameSite=Strict session cookie (12 h), CSRF header
  on every API call, strict CSP, login rate limiting. Changing the password signs out all sessions.
- Behind Cloudflare the per-IP login limit is weak (nginx sees the CDN address); the global limit and
  the secret path still apply. For stricter access, `main.py panel --remove` and reach it by
  `ssh -L 8088:127.0.0.1:<panel_port> ...` instead.
- No build step: static files in `panel/static/`, Python standard library only.

## The `kk` dashboard

```
[1] Create SSH/websocket user     [6] Add custom port
[2] Delete user                   [7] Remove custom port
[3] List users                    [8] Show active ports
[4] Live connections / bandwidth  [9] Server status (services)
[5] Internet speed test           [0] Exit
```

## Project layout

```
main.py             CLI entry point
install.sh          bootstrap installer for a fresh VPS
core/               config + state store, logger, shell wrapper, exceptions, plugin manager,
                    doctor (self-check/repair), uninstall, blocklists
features/           one module per installable feature (auto-discovered)
panel/              optional web panel (server, API, auth, metrics, static UI)
dashboard/          the `kk` menu: users, ports, monitor, UI helpers
templates/          nginx config templates
data/banners.txt    pool of SSH banners
tests/              pytest suite
```

State lives in `/var/lib/sshauto/state.json`, logs in `/var/log/sshauto/`.

## Design constraints

- System DNS is never modified.
- Certificates: Cloudflare Origin Certificate or self-signed only. No ACME fallback.
- No auto-update mechanism.

## Adding a feature

Drop a module in `features/` and subclass `BaseFeature`. `PluginManager` discovers it
and orders installs by `depends_on`. Set `optional = True` to make it opt-in.

```python
from features.base import BaseFeature

class MyThingFeature(BaseFeature):
    name = "my_thing"
    description = "Shown during install"
    depends_on = ["packages"]

    def is_installed(self) -> bool: ...
    def install(self) -> None: ...
    def remove(self) -> None: ...
```

## Development

```bash
pip install -r requirements-dev.txt
make check        # ruff + shellcheck + pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
