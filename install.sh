#!/usr/bin/env bash
# sshauto bootstrap installer. Fully automated; never modifies system DNS.
#
#   bash <(curl -LS https://raw.githubusercontent.com/blackystrngr/SSHAuto/main/install.sh)
#
# Everything is on by default. Override with environment variables:
#   SSHAUTO_DOMAIN       domain for the tunnel (skips the prompt)       e.g. hi.blackstrngr.qzz.io
#   SSHAUTO_CF_EMAIL     Cloudflare account email   } both set -> Cloudflare Origin cert,
#   SSHAUTO_CF_KEY       Cloudflare Global API key  } otherwise a self-signed cert
#   SSHAUTO_SQUID=0      skip the Squid proxy        (SSHAUTO_SQUID_USER / _PASS / _PORT to customise)
#   SSHAUTO_FILTER=0     skip the ad/malware/phishing/miner DNS filter
#   SSHAUTO_PANEL=0      skip the web panel          (SSHAUTO_PANEL_USER / _PASS to customise)
#   SSHAUTO_REPO_URL, SSHAUTO_BRANCH
set -euo pipefail

REPO_URL="${SSHAUTO_REPO_URL:-https://github.com/blackystrngr/SSHAuto.git}"
BRANCH="${SSHAUTO_BRANCH:-main}"
APP_ROOT="/opt/sshauto"

SQUID="${SSHAUTO_SQUID:-1}"
FILTER="${SSHAUTO_FILTER:-1}"
PANEL="${SSHAUTO_PANEL:-1}"

c_red()   { printf '\033[31m%s\033[0m\n' "$1"; }
c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_cyan()  { printf '\033[36m%s\033[0m\n' "$1"; }

if [[ $EUID -ne 0 ]]; then
    c_red "This installer must run as root (try: sudo bash install.sh)"
    exit 1
fi

if ! grep -qiE 'debian|ubuntu' /etc/os-release 2>/dev/null; then
    c_red "Only Debian/Ubuntu family systems are supported."
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

c_cyan "==> Installing bootstrap dependencies"
apt-get update -y
apt-get install -y python3 python3-pip git curl wget ca-certificates

c_cyan "==> Removing any stuck Git proxy settings"
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

c_cyan "==> Fetching sshauto into ${APP_ROOT}"
rm -rf "${APP_ROOT}"
git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_ROOT}"
chmod +x "${APP_ROOT}/main.py"

c_cyan "==> Installing Python dependencies"
if [[ -f "${APP_ROOT}/requirements.txt" ]]; then
    pip3 install --break-system-packages --upgrade -r "${APP_ROOT}/requirements.txt" \
        || pip3 install --upgrade -r "${APP_ROOT}/requirements.txt" \
        || c_red "Warning: pip install failed; sshauto will retry what it needs."
fi

# Ask for the domain only if it was not supplied and we have a terminal to ask on.
if [[ -z "${SSHAUTO_DOMAIN:-}" && ! -t 0 && ! -r /dev/tty ]]; then
    c_red "No terminal to ask for the domain. Re-run with SSHAUTO_DOMAIN=your.domain"
    exit 1
fi

args=(install --force)
[[ "$SQUID" != "0" ]]  && args+=(--squid)
[[ -n "${SSHAUTO_SQUID_USER:-}" ]] && args+=(--squid-user "$SSHAUTO_SQUID_USER")
[[ -n "${SSHAUTO_SQUID_PASS:-}" ]] && args+=(--squid-pass "$SSHAUTO_SQUID_PASS")
[[ -n "${SSHAUTO_SQUID_PORT:-}" ]] && args+=(--squid-port "$SSHAUTO_SQUID_PORT")
[[ "$FILTER" != "0" ]] && args+=(--filter)
[[ "$PANEL" != "0" ]]  && args+=(--panel)
[[ -n "${SSHAUTO_PANEL_USER:-}" ]] && args+=(--panel-user "$SSHAUTO_PANEL_USER")
[[ -n "${SSHAUTO_PANEL_PASS:-}" ]] && args+=(--panel-pass "$SSHAUTO_PANEL_PASS")

c_cyan "==> Running sshauto ${args[*]//${SSHAUTO_PANEL_PASS:-@@none@@}/***}"
if [[ ! -t 0 && -r /dev/tty ]]; then
    python3 "${APP_ROOT}/main.py" "${args[@]}" < /dev/tty
else
    python3 "${APP_ROOT}/main.py" "${args[@]}"
fi

c_green "==> Done. Type 'kk' for the terminal dashboard."
[[ "$PANEL" != "0" ]] && c_green "    Web panel: python3 ${APP_ROOT}/main.py panel --status"
exit 0
