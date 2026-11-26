# sshauto

[![CI](https://github.com/blackystrngr/SSHAuto/actions/workflows/ci.yml/badge.svg)](https://github.com/blackystrngr/SSHAuto/actions/workflows/ci.yml)

SSH-over-WebSocket relay autoscript for Debian/Ubuntu VPS. One command installs and
wires together nginx, a Python WebSocket proxy, Dropbear, OpenSSH, fail2ban, iptables
rules and TLS certificates, then gives you a `kk` dashboard to manage users and ports.

> **Use responsibly.** Run this only on infrastructure you own, in line with your
> upstream providers' terms of service and local law.

## Architecture

```
client ─▶ CDN ─▶ nginx (HTTP/HTTPS relay ports) ─▶ Python proxy (127.0.0.1:9955)
                                                        │
                                                        └─▶ Dropbear (127.0.0.1:110)
```

| Component | Role |
|---|---|
| nginx | Public entry point on all HTTP/HTTPS relay ports, catch-all `server_name`, no buffering |
| Python proxy | Handles WebSocket upgrade, HTTP `CONNECT` and plain HTTP forwarding (systemd: `ws-ssh-proxy`) |
| Dropbear | Tunnel SSH backend, loopback only (systemd: `dropbear-tunnel`) |
| OpenSSH | Direct admin access on its own port, with a random banner |
| fail2ban | Jails for sshd and Dropbear |
| iptables | Rules are added, never flushed |
| Certificates | Cloudflare Origin Certificate (15 years) or self-signed, chosen from an interactive menu |
| udpgw | Optional UDP forwarding via badvpn-udpgw |
| Squid | Optional authenticated proxy, see below |

## Install

On the target VPS, as root:

```bash
bash <(curl -LS https://raw.githubusercontent.com/blackystrngr/SSHAuto/main/install.sh)
```

Or from a clone:

```bash
sudo python3 main.py install
```

You will be asked for a domain and a certificate type. When it finishes, type `kk`.

## Commands

```bash
sudo python3 main.py install [--only a,b] [--force] [--quiet] [--skip-non-idempotent]
sudo python3 main.py install --squid     # also install the optional Squid proxy
sudo python3 main.py squid               # install/reconfigure Squid on its own
sudo python3 main.py status              # per-feature install status
sudo python3 main.py cert                # re-run the certificate menu
sudo python3 main.py uninstall           # best-effort teardown
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
core/               config + state store, logger, shell wrapper, exceptions, plugin manager
features/           one module per installable feature (auto-discovered)
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
