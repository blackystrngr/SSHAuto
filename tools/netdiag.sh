#!/usr/bin/env bash
# Read-only network/DNS evidence for the content_filter install problem. Changes nothing.
# Usage: sudo bash tools/netdiag.sh 2>&1 | tee /tmp/netdiag.txt
U1=https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq/tif.mini.txt
U2=https://raw.githubusercontent.com/blocklistproject/Lists/master/alt-version/ads-nl.txt
sec() { printf '\n=== %s ===\n' "$*"; }
sec "time / uptime";            date -u; uptime
sec "ip -4 route";              ip -4 route
sec "ip -6 route";              ip -6 route
sec "ip addr (brief)";          ip -br addr
sec "system resolv.conf";       cat /etc/resolv.conf
sec "tunnel resolv.conf";       cat /etc/sshauto/filter/resolv.conf 2>&1
sec "getent ahosts github raw"; getent ahosts raw.githubusercontent.com
sec "curl -4 (10s limit)"
for u in "$U1" "$U2"; do curl -4 -sS -o /dev/null -m 10 -w "%{http_code} connect=%{time_connect}s total=%{time_total}s  $u\n" "$u" 2>&1; done
sec "curl -6 (10s limit)"
curl -6 -sS -o /dev/null -m 10 -w "%{http_code} $U1\n" "$U1" 2>&1
sec "curl -4 other hosts";      for h in https://1.1.1.1 https://github.com https://archive.ubuntu.com; do curl -4 -sS -o /dev/null -m 8 -w "%{http_code} connect=%{time_connect}s  $h\n" "$h" 2>&1; done
sec "iptables policy + OUTPUT"; iptables -S 2>&1 | grep -E '^-P|OUTPUT'
sec "iptables INPUT (first 25)"; iptables -S INPUT 2>&1 | head -25
sec "conntrack"; cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max 2>&1
sec "dmesg (net/conntrack)";    dmesg 2>/dev/null | grep -i -E 'conntrack|table full|martian|drop' | tail -10
sec "dns filter service";       systemctl is-active sshauto-dnsfilter 2>&1; journalctl -u sshauto-dnsfilter -n 15 --no-pager 2>&1
sec "listeners on :53";         ss -lunp 2>/dev/null | grep -E ':53\b'; ss -ltnp 2>/dev/null | grep -E ':53\b'
sec "tunnel units";             systemctl is-active ws-ssh-proxy dropbear-tunnel 2>&1
sec "proxy env (should be empty)"; env | grep -i -E '^(https?|all|no)_proxy=' || echo none
sec "default-route probe";      ip -4 route get 185.199.108.133
sec "ping gw / 1.1.1.1";        ping -c2 -W2 1.1.1.1 2>&1 | tail -3
