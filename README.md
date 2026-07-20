# sshauto

Fully automated SSH-over-websocket relay autoscript for a Debian/Ubuntu
VPS. Installs and wires together nginx, dropbear, OpenSSH, fail2ban,
iptables, and a certificate manager, then hands you a `kk` dashboard to
manage users/ports/traffic — and keeps itself updated within 30 seconds
of any new commit to this repo.

> **Use responsibly.** Domain fronting / CDN relaying is legitimate,
> widely-used infrastructure (privacy tools, anti-censorship, ordinary
> VPN products all rely on it) — but it's your responsibility to run
> this only on infrastructure you own, in line with your upstream
> providers' terms of service and local law.

## Architecture

```
Client ──TLS/HTTP──▶  CDN edge  ──▶  nginx (this VPS, listens on ALL of
                                      HTTP_PORTS + HTTPS_PORTS, any IP)
                                          │
                                          │ websocket-upgrade request,
                                          │ proxy_buffering off, raw bytes
                                          ▼
                                  dropbear on 127.0.0.1:<port>
                                  (never exposed publicly — the whole
                                   point of fronting it through nginx)
```

- **nginx** is the relay. It doesn't care what domain/SNI the client
  used (`server_name _;` catch-all) — any request with
  `Upgrade: websocket` on any configured port gets forwarded, byte for
  byte, to dropbear on loopback. `proxy_buffering off` +
  `proxy_request_buffering off` keep the tunnel effectively instant in
  both directions.
- **dropbear** is bound to `127.0.0.1` only. It never has a public
  firewall rule — nginx is the only way in, which is what makes the
  CDN-fronting trick work (from the outside this looks like ordinary
  web traffic on 443/2083/8443/etc).
- **OpenSSH** stays available for direct, non-fronted admin access on
  its own port, separately hardened and banner'd.
- **Certificates**: self-signed / ACME (certbot, auto-registers an
  account) / Cloudflare Origin CA (email + Global API Key). If a valid
  cert already exists on disk for the chosen domain, the menu is
  skipped automatically and the existing cert is reused.
- **fail2ban** watches both sshd and dropbear's auth log lines.
- **iptables** default-denies inbound IPv4 except SSH + the relay
  ports; IPv6 is dropped entirely (ip6tables policy + kernel
  `disable_ipv6` sysctl, belt and suspenders).
- **Auto-update**: a systemd timer (`sshauto-autoupdate.timer`) polls
  `git fetch` every 30 seconds; a new commit triggers `git pull` and an
  idempotent re-install of every feature except certificates (re-issuing
  certs is never done automatically).

## Project layout

```
main.py                    CLI entry point (install/update/status/cert/dashboard)
install.sh                 curl | bash bootstrap for a fresh VPS
core/
  config.py                 all constants + JSON state store (/etc/sshauto/state.json)
  logger.py                  colored logger (INFO/SUCCESS/WARNING/IMPORTANT/ERROR/CRITICAL)
  shell.py                    subprocess wrapper: retries, timeouts, dry-run, typed errors
  exceptions.py                 exception hierarchy
  plugin_manager.py              auto-discovers features/*.py, topological install order
features/
  base.py                    BaseFeature contract every plugin implements
  packages.py                 apt install required / purge apache*, ufw, firewalld
  firewall.py                  iptables + full IPv6 block
  ssh_service.py                 OpenSSH: port, random banner, hardening
  dropbear_service.py             dropbear on 127.0.0.1 only
  nginx_relay.py                   generates the HTTP+HTTPS relay config
  certificates.py                   self-signed / ACME / Cloudflare strategies
  fail2ban_service.py                jails for sshd + dropbear
  autoupdate.py                       installs the 30s systemd timer
dashboard/
  app.py                      the `kk` menu loop
  users.py                     create/list/delete tunnel accounts
  ports.py                      add/remove custom relay ports (nginx+firewall together)
  monitor.py                     live connections, user counts, throughput, speed test
  ui.py                           dependency-free terminal rendering helpers
templates/                  nginx + systemd unit templates (token-based, no Jinja needed)
scripts/autoupdate_check.py the script the systemd timer runs every 30s
data/banners.txt            pool of random SSH banners
```

## Install (on the target VPS, as root)

```bash
bash <(curl -LS https://raw.githubusercontent.com/blackystrngr/sshauto/main/install.sh)
```

or, if you've already cloned it:

```bash
sudo python3 main.py install
```

At the end you'll be asked for a domain and a certificate strategy
(skipped automatically if a valid cert is already on disk), then:

```
Setup complete. Type 'kk' any time to open the dashboard.
```

## The `kk` dashboard

```
$ kk
  Active tunnels        3
  Total accounts        12
  Throughput             ↓ 812.4 kbps   ↑ 140.2 kbps

  [1] Create SSH/websocket user
  [2] Delete user
  [3] List users
  [4] Live connections / bandwidth
  [5] Internet speed test
  [6] Add custom port
  [7] Remove custom port
  [8] Show active ports
  [9] Server status (services)
  [0] Exit
```

## Adding a new feature

Drop a file in `features/`, subclass `BaseFeature`, done — no
registration step, `PluginManager` finds it via `pkgutil` and slots it
into the dependency-ordered install sequence automatically:

```python
# features/my_thing.py
from features.base import BaseFeature

class MyThingFeature(BaseFeature):
    name = "my_thing"
    description = "One-line description shown during install"
    depends_on = ["packages"]          # installed after these

    def is_installed(self) -> bool: ...
    def install(self) -> None: ...
    def remove(self) -> None: ...
```

## Other useful commands

```bash
sudo python3 main.py status        # per-feature install status
sudo python3 main.py cert          # re-run the certificate wizard
sudo python3 main.py update        # manually trigger what the 30s timer does
sudo python3 main.py uninstall     # best-effort teardown
```

## Notes / what's intentionally left for you to wire up

- `install.sh`'s `REPO_URL` is a placeholder — point it at your actual
  git remote before using the one-line installer.
- `AcmeStrategy` uses certbot `--standalone` (briefly stops nginx during
  the HTTP-01 challenge). Swap to `--webroot` or a DNS-01 plugin if you
  need zero-downtime issuance.
- The Cloudflare strategy issues an **Origin CA** cert (15-year
  validity, works with "Full (strict)" SSL mode) rather than going
  through ACME's DNS-01 — simpler and it's what the Global API Key is
  actually for.
- This is the first build: every feature here is real, tested code (the
  nginx relay was verified end-to-end against a live nginx + a fake
  dropbear backend), but on a fresh VPS run `main.py status` after
  install and skim `/var/log/sshauto/autoupdate.log` the first few times
  to confirm your distro's paths match (`sshd` vs `ssh` service name,
  `/var/log/auth.log` vs `/var/log/secure`, etc. — both are already
  handled, but worth a glance).
