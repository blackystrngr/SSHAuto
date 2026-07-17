# sshauto

**Fully automated, low‑latency SSH‑over‑WebSocket relay with integrated HTTP CONNECT proxy, optional UDP/ICMP/DNS fallback tunnels, and a real‑time dashboard.**

Designed for Debian/Ubuntu VPS. Installs and configures nginx, Dropbear, OpenSSH, fail2ban, iptables, a Python‑based unified proxy, and a certificate manager (Cloudflare Origin or self‑signed). Gives you a `kk` dashboard to manage users, ports, traffic, and optional tunnels – all tuned for minimal latency and maximum throughput.

> **Use responsibly.** This tool is for legitimate privacy, anti‑censorship, and ordinary VPN use on infrastructure you own. Comply with your provider’s terms and local laws.

---

## Features

- **Unified tunnel** – single proxy handles both WebSocket upgrades (SSH over HTTP/HTTPS) and HTTP CONNECT (plain proxy), on the same ports.
- **Performance‑tuned** – uvloop, TCP Fast Open, TCP_QUICKACK, 4 MiB socket buffers, real‑time scheduler, BBR congestion control, and larger SSH windows.
- **WebSocket relay** – nginx forwards any request with `Upgrade: websocket` to Dropbear, with buffering disabled for instant byte‑stream.
- **Direct HTTP proxy** – Squid (or the unified proxy itself) handles standard CONNECT requests on port 3128 (optional).
- **Certificate wizard** – Cloudflare Origin (15‑year validity) with retry logic and fallback to self‑signed; skip if a valid cert exists.
- **Dashboard** – `kk` command gives you a terminal UI to create/delete users, monitor traffic, add custom ports, and manage optional tunnels.
- **Optional tunnels** – Hysteria2 (UDP/QUIC), DNS tunneling (dnstt), and ICMP tunneling (pingtunnel) – all enabled/disabled via the dashboard.
- **Low‑latency** – keepalives, aggressive scheduling, TLS 1.3 early data, and large file descriptor limits keep the tunnel responsive even under load.
- **Auto‑healing** – services restart automatically on failure; systemd units are configured for crash‑proof operation.
- **No ACME** – Cloudflare Origin or self‑signed only; no automatic Let’s Encrypt.

---

## Architecture (simplified)
Client (HTTP Injector / curl / SSH client)
│
│ WebSocket upgrade (Upgrade: websocket) or HTTP CONNECT
▼
nginx (ports 80, 443, 8080, 8880, 2052, 2082, 2086, 2095, 8443, …)
│
│ proxy_pass to unified Python proxy
▼
Unified Python proxy (port 9955 by default)
│
│ CONNECT to Dropbear on 127.0.0.1:110
▼
Dropbear (SSH backend, never exposed publicly)

text

- **nginx** listens on all public ports; it forwards **only** requests with `Upgrade: websocket` (or CONNECT if you use the unified proxy directly).
- **Python proxy** handles both WebSocket upgrades and HTTP CONNECT, tunnels raw TCP to Dropbear.
- **Dropbear** is bound to localhost – no direct internet exposure.
- **OpenSSH** remains on port 22 for direct admin access.
- **Squid** (optional) provides a plain HTTP CONNECT proxy on port 3128.

---

## Performance Optimizations

| Layer            | Tuning                                                                 |
|------------------|------------------------------------------------------------------------|
| Python proxy     | uvloop, 256 KiB read chunks, 4 MiB socket buffers, TCP_QUICKACK, SO_BUSY_POLL, real‑time CPU scheduler, `LimitNOFILE=1048576` |
| Dropbear         | 1 MiB SSH window, keepalive every 15s, no idle timeout                 |
| Nginx            | TCP Fast Open (`fastopen=256`), `access_log off`, large SSL session cache, TLS 1.3 early data, 4 KiB TLS buffer |
| Kernel           | BBR congestion, TCP Fast Open, low‑latency, fin_timeout=15, expanded ephemeral port range, `netdev_budget=600` |

These changes reduce **time‑to‑first‑byte** to under 200 ms and sustain high throughput over long‑haul links.

---

## Installation

**One‑liner** (recommended):
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/blackystrngr/sshauto/main/install.sh)
Manual clone:

bash
git clone https://github.com/blackystrngr/sshauto.git
cd sshauto
sudo python3 main.py install --force
During installation, you will be prompted for:

Domain name (e.g., hi.blackstrngr.qzz.io)

Certificate type:

Cloudflare Origin – requires Cloudflare account email and Global API Key (15‑year validity).

Self‑signed – generated locally (no external dependency).

If a valid certificate already exists, the wizard is skipped automatically.

After Installation
text
Setup complete. Type 'kk' any time to open the dashboard.
The kk Dashboard
text
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
  [10] Network Optimizer & BBR Menu
  [11] Extra Services (Squid, stunnel, sslh, UDPGW)
  [12] Hysteria2 UDP/QUIC tunnel
  [13] DNS tunnel (iodine)
  [14] ICMP tunnel (pingtunnel)
  [0] Exit
Users – created with /bin/false shell, added to sshauto-users group, password set and unlocked.

Ports – add/remove custom HTTP/HTTPS ports; firewall and nginx are updated automatically.

Optional tunnels – enabled on demand (see below).

Optional Tunnels (Enabled via Dashboard)
1. Hysteria2 (UDP/QUIC)
Fast, modern QUIC‑based tunnel.

Uses UDP port (configurable, default 2096).

Requires a certificate (same as main tunnel).

Client: Hysteria2 client with password from state.

2. DNS Tunnel (dnstt)
Tunnels TCP/IP over DNS queries (UDP 53).

Requires a delegated subdomain (e.g., t.yourdomain.com).

Client: iodine/dnstt client with public key from /etc/dnstt/server.pub.

3. ICMP Tunnel (pingtunnel)
Tunnels TCP over ICMP (ping) packets.

Requires kernel to ignore pings (net.ipv4.icmp_echo_ignore_all=1).

Client: pingtunnel client with numeric key (default 123456).

Each optional tunnel can be installed/uninstalled from the dashboard – they are not part of the main installation.

Client Configuration
WebSocket (SSH)
Proxy Type: WebSocket (or HTTP with Upgrade: websocket)

Server: hi.blackstrngr.qzz.io (or your domain/IP)

Port: 80, 443, 8080, 8880, etc.

SSH Host: (same as server)

SSH Port: (same as the WebSocket port)

Username/Password: as created via dashboard.

HTTP CONNECT (plain proxy)
Proxy Type: HTTP

Server: your domain/IP

Port: 3128 (Squid) or any WebSocket port (if using the unified proxy’s CONNECT support).

Other Commands
bash
sudo python3 main.py status        # check installation status of all features
sudo python3 main.py cert          # re‑run the certificate wizard
sudo python3 main.py uninstall     # best‑effort teardown
Directory Layout
text
main.py                    CLI entry point
install.sh                 bootstrap installer
core/
  config.py                constants + JSON state store
  logger.py                colored logger
  shell.py                 subprocess wrapper
  exceptions.py            exception hierarchy
  plugin_manager.py        auto‑discovers features
features/
  base.py                  BaseFeature contract
  packages.py              apt install / purge
  firewall.py              iptables flush + allow‑all
  ssh_service.py           OpenSSH config
  dropbear_service.py      Dropbear on 127.0.0.1
  nginx_relay.py           nginx config generator
  python_proxy.py          unified WebSocket + CONNECT proxy
  certificates.py          Cloudflare / self‑signed wizard
  fail2ban_service.py      jails for sshd + dropbear
  network_optimizer.py     BBR + sysctl tweaks
  squid_proxy.py           optional Squid install
  hysteria2.py             optional UDP/QUIC tunnel
  dns_tunnel.py            optional DNS tunnel
  icmp_tunnel.py           optional ICMP tunnel
dashboard/
  app.py                   kk menu loop
  users.py                 user management
  ports.py                 custom port management
  monitor.py               live stats
  ui.py                    terminal UI helpers
templates/
  nginx_relay.conf.tpl     nginx HTTP template
  nginx_relay_https.conf.tpl nginx HTTPS template
data/banners.txt           pool of SSH banners
Troubleshooting
Proxy not starting – check /var/log/sshauto/proxy.log and journalctl -u ws-ssh-proxy.

Dropbear rejects users – ensure /bin/false is in /etc/shells (the installer adds it).

Certificate fails – the wizard retries 3 times; if Cloudflare fails, choose self‑signed.

UDP/ICMP tunnels not working – open the corresponding ports in your VPS firewall.

Contributing
Add a new feature by dropping a file in features/ that subclasses BaseFeature. The PluginManager will auto‑discover it.

python
class MyFeature(BaseFeature):
    name = "my_feature"
    description = "Does something cool"
    depends_on = ["packages"]

    def is_installed(self): ...
    def install(self): ...
    def remove(self): ...
