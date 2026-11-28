# Changelog

## Unreleased

### Changed
- **Squid is opt-in too.** `install.sh` no longer passes `--squid` by default (set `SSHAUTO_SQUID=1` to include it); `main.py install` already needed `--squid`.
- **DNS filter is fully opt-in.** `install.sh` no longer passes `--filter` by default (set `SSHAUTO_FILTER=1` to include it); `main.py install` already needed `--filter`. Nothing of the filter (dnsmasq-base, lists, units, drop-ins) is touched unless you ask for it.
- DNS filter hardening: the filtered resolver is still only bind-mounted into `dropbear-tunnel` and `ws-ssh-proxy` (system `/etc/resolv.conf`, systemd-resolved and netplan are never touched). Its resolv.conf now lists the host's own nameservers after the filter as a fail-open fallback (`timeout:1 attempts:2`), and dnsmasq's upstream is pinned to the system `/etc/resolv.conf`.
- **Firewall is optional and plain iptables again (no ufw).** Turn it on from the web panel, `main.py firewall` or `install --firewall`. It only adds `ACCEPT` rules for SSH, DNS 53 and the relay/Squid/custom ports at the top of `INPUT` (in front of Oracle's catch-all `REJECT`, which is left in place) and saves them with `netfilter-persistent`. No flush, no default-policy change, no rule is deleted; ufw is not used and `core/ufw.py` is gone. fail2ban uses the `iptables-multiport` ban action. Optional: `SSHAUTO_TUNE_CONNTRACK=1`.
- The web panel is always installed last (`priority` on features).
- Uninstall (and port/Squid removal) never delete firewall rules; certificates and udpgw are kept on uninstall; fail2ban is no longer restarted/purged on uninstall.
- **fail2ban is now optional and off by default**, like Squid, the content filter and the web panel. Turn it on/off with `main.py fail2ban [--remove]`, `main.py install --fail2ban`, or the Protection page in the web panel (`GET/POST/DELETE /fail2ban`). The `fail2ban` apt package is still installed as a base dependency, but no jail is configured or started until you turn it on.

### Security
- Every tunnel is logged (`relay.tunnel`, always on): real client IP (CF-Connecting-IP behind Cloudflare), relay port and the Dropbear backend port. Dropbear logs logins as `127.0.0.1:<backend_port>`, so `python3 tools/sessions.py` shows which real IP used which SSH account.
- Relay is no longer an open proxy: `CONNECT` is accepted only for this server's own address (-> Dropbear/sshd), and plain HTTP for other hosts gets a static 200 page instead of being forwarded. Every tunnel now requires an SSH account.
- udpgw binds `127.0.0.1:7300` instead of `0.0.0.0:7300` (it has no authentication); clients reach it through the SSH tunnel. Existing units bound to 0.0.0.0 are rewritten on the next install run.

### Changed
- **nginx removed completely** (feature, templates, packages, panel snippet). The relay is the only public listener.
- New relay (`relay/ws_ssh_proxy.py`, a real module with tests instead of generated code): binds every relay port, sniffs TLS vs plain vs raw SSH per connection, so TLS works on any port and plain works on any port; TLS 1.0+, no ALPN restriction, SNI ignored; SSH-over-TLS; WebSocket with `Sec-WebSocket-Accept`, raw-after-101 and RFC 6455 framed clients; `CONNECT` to own address maps to Dropbear; silent (server-first) clients get Dropbear's banner.
- The web panel is served by the relay itself (secret path, HTTPS only, real client IP from Cloudflare).
- Doctor probes every relay port with and without TLS, firewall rules, and the panel through the relay.
- Panel website: tunnel path is Clients → Relay → Dropbear → Internet; new "What the relay accepts" section; relay TLS state.

### Fixed
- Connections failing after a reboot when iptables rules were reloaded without the relay ports (relay re-applies them).
- HTTP forwarding could loop back into the relay itself and exhaust file descriptors; own addresses now get a page, private targets are refused, loop header added.
- Descriptor leaks (half-closed tunnels, silent clients, header slow-loris), `LimitNOFILE` raised.
- Old nginx site files are removed on install/uninstall.

## 1.0.0

### Added
- Optional Squid proxy (`install --squid`, `main.py squid`); authentication always required, default `didntgo` / `didntgo1#`.
- `optional` flag on features; `--version` flag.
- Test suite, ruff, shellcheck, GitHub Actions CI, Makefile, pyproject.

### Added (web panel)
- Optional `web_panel` feature (`install --panel`, `main.py panel`): modern responsive UI (light/dark) for monitoring and management; loopback-only server behind a secret nginx path; scrypt auth, signed sessions, CSRF header, CSP, rate limiting. `doctor` and `uninstall` cover it.

### Fixed
- User management: expiry semantics, shell injection in password change, tunnel connections double-counted.

### Added (content filter)
- Optional `content_filter` feature: DNS blocking of ads, malware, phishing and cryptominers for tunnel traffic (`install --filter`, `main.py filter`), per-category switches, allowlist, `--block-private` (RFC1918 / link-local / metadata), `--update`, `--status`, `--remove`.
- Runs a loopback-only dnsmasq applied to `dropbear-tunnel` and `ws-ssh-proxy` through systemd drop-ins; system DNS is not modified.
- `doctor` checks the filter (service, resolver answers, blocking works, tunnel services use it); `uninstall` removes it.

### Added (uninstall)
- `main.py uninstall` removes everything sshauto built: tunnel users, services and units, nginx site, fail2ban jails, SSH hardening/banner, certificates, state, logs, `kk`, custom-port firewall rules, and `/opt/sshauto` when run from there. Asks for confirmation (`--yes` to skip). `--keep-users`, `--purge-packages` (nginx, dropbear, fail2ban, squid).
- Safety: SSH port and base HTTP/HTTPS firewall rules are left alone; Squid is only touched if sshauto configured it; sshd config is validated (`sshd -t`) before restart and rolled back on error.

### Added (self-healing)
- `main.py doctor`: tests services, Dropbear loopback binding, and a real WebSocket handshake through the proxy and through nginx (expects `101` + SSH banner); reinstalls broken features and re-checks. `--no-heal` to only check.
- `install` runs `doctor` automatically at the end and exits non-zero if the tunnel is not working.
- Features whose dependency failed are skipped (e.g. nginx no longer configured against a missing proxy); idempotent features are retried once.

### Changed
- Python proxy now listens on `127.0.0.1` only; nginx is the sole public listener.
- uvloop is mandatory (installer tries pip, then apt, and fails the step if it can't be installed).
- Proxy tuning: 64 KB reads, 256 KB stream buffers, backlog 1024.

### Fixed
- Distro `dropbear.service` was left enabled and grabbed port 110 on 0.0.0.0, so `dropbear-tunnel` (127.0.0.1) failed with "Address already in use". It is now disabled and masked, and `NO_START=1` is written.
- Proxy no longer drops a connection on a malformed header line.
- `datetime.UTC` crash on Python < 3.11.
- `Shell.run(check=False)` raising on timeout (broke `python_proxy` when the `uvloop` pip install was slow).
- `str | None` annotations failing on Python 3.9 in `core/exceptions.py`.
- Squid port was never opened in iptables; Squid removal deleted its config; unchanged config skipped service start.
- `--skip-non-idempotent` selected every feature, including optional ones.
- Bare `except:` and other lint issues.
