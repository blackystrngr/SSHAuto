text
# sshauto

**SSH-over-WebSocket relay with a built‑in dashboard, performance tuning, and optional UDP/DNS/ICMP tunnels.**

- One‑line install on Debian/Ubuntu VPS
- WebSocket relay (nginx → Python proxy → Dropbear)
- HTTP CONNECT proxy on the same ports
- Cloudflare Origin or self‑signed certificates
- `kk` dashboard to manage users, ports, and tunnels
- Optimised for low latency and high throughput

---

## Quick Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/blackystrngr/sshauto/main/install.sh)
After installation, type kk to open the dashboard.

What It Does
nginx listens on public ports (80, 443, 8080, …)

Python proxy handles Upgrade: websocket and HTTP CONNECT

Dropbear runs on localhost:110 (never exposed)

OpenSSH stays on port 22 for admin access

Dashboard creates users, monitors traffic, adds custom ports

Dashboard
text
$ kk
  Active tunnels        3
  Total accounts        12
  Throughput             ↓ 812 kbps   ↑ 140 kbps

  [1] Create user
  [2] Delete user
  [3] List users
  [4] Live connections
  [5] Speed test
  [6] Add custom port
  [7] Remove custom port
  [8] Show ports
  [9] Service status
  [10] Network optimizer
  [11] Extra services (Squid, stunnel, sslh, UDPGW)
  [12] Hysteria2 (UDP)
  [13] DNS tunnel
  [14] ICMP tunnel
  [0] Exit
Optional Tunnels (via Dashboard)
Tunnel	Protocol	Port	Client
Hysteria2	UDP/QUIC	2096	Hysteria2 client
DNS tunnel	DNS	UDP 53	iodine / dnstt
ICMP tunnel	ICMP	–	pingtunnel
Commands
bash
sudo python3 main.py install          # full install
sudo python3 main.py cert             # run certificate wizard
sudo python3 main.py status           # check all services
sudo python3 main.py uninstall        # remove everything (best‑effort)
Performance
uvloop, TCP Fast Open, TCP_QUICKACK, real‑time scheduler, 4 MiB buffers

BBR congestion control, expanded ephemeral ports, fin_timeout=15

Dropbear 1 MiB window, keepalive every 15s

Nginx TLS 1.3 early data, session cache, fastopen=256

