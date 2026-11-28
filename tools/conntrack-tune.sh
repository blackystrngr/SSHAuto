#!/usr/bin/env bash
# Raise the connection-tracking limit and shorten closing-state timeouts so mass tunnel connections do not hit
# "nf_conntrack: table full, dropping packet". Run as root. Re-running is safe.
#   NOTRACK_LO=0  skip exempting loopback (relay <-> Dropbear) traffic from tracking
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo bash $0"; exit 1; }
CT=/proc/sys/net/netfilter
[[ -r $CT/nf_conntrack_max ]] || { modprobe nf_conntrack 2>/dev/null || true; }
[[ -r $CT/nf_conntrack_max ]] || { echo "nf_conntrack is not loaded; nothing to tune"; exit 0; }

show() { echo "$1: count=$(cat $CT/nf_conntrack_count) max=$(cat $CT/nf_conntrack_max) buckets=$(cat /sys/module/nf_conntrack/parameters/hashsize 2>/dev/null || echo ?)"; }
show before

mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
max=$(( mem_mb * 1024 * 1024 / 8 / 320 ))         # ~320 bytes per entry, at most 1/8 of RAM
(( max < 65536 )) && max=65536
(( max > 524288 )) && max=524288
buckets=$(( max / 4 ))

cat > /etc/sysctl.d/99-sshauto-conntrack.conf <<CONF
net.netfilter.nf_conntrack_max = $max
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_close_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_close = 10
net.netfilter.nf_conntrack_tcp_timeout_syn_sent = 30
net.netfilter.nf_conntrack_tcp_timeout_syn_recv = 30
net.netfilter.nf_conntrack_tcp_timeout_established = 86400
CONF
echo nf_conntrack > /etc/modules-load.d/sshauto-conntrack.conf
echo "options nf_conntrack hashsize=$buckets" > /etc/modprobe.d/sshauto-conntrack.conf
echo "$buckets" > /sys/module/nf_conntrack/parameters/hashsize 2>/dev/null || true
sysctl -q -p /etc/sysctl.d/99-sshauto-conntrack.conf

if [[ "${NOTRACK_LO:-1}" == "1" ]]; then
  iptables -w 5 -t raw -C PREROUTING -i lo -j NOTRACK 2>/dev/null || iptables -w 5 -t raw -I PREROUTING -i lo -j NOTRACK || echo "raw table unavailable: loopback NOTRACK skipped"
  iptables -w 5 -t raw -C OUTPUT -o lo -j NOTRACK 2>/dev/null || iptables -w 5 -t raw -I OUTPUT -o lo -j NOTRACK || true
fi
if command -v netfilter-persistent >/dev/null; then netfilter-persistent save >/dev/null 2>&1 || true
else mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4; fi
show after
