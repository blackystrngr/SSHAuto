#!/usr/bin/env bash
# sshauto bootstrap installer – hardened for DNS reliability.
set -euo pipefail

REPO_URL="https://github.com/blackystrngr/SSHAuto"
BRANCH="main"
APP_ROOT="/opt/sshauto"

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

# ---- 1. HARDEN DNS (survives reboots, systemd, network restarts) ----
c_cyan "==> Setting reliable DNS resolvers (8.8.8.8, 1.1.1.1)..."
# Override /etc/resolv.conf directly (for legacy tools)
echo "nameserver 8.8.8.8" > /etc/resolv.conf
echo "nameserver 1.1.1.1" >> /etc/resolv.conf
chattr +i /etc/resolv.conf 2>/dev/null || true  # prevent overwrite

# Also set via systemd-resolved (if active)
if command -v resolvectl &>/dev/null; then
    resolvectl set-dns 8.8.8.8 1.1.1.1
    resolvectl set-domain ~.
    resolvectl flush-caches
fi

# Flush DNS cache (multiple methods)
if command -v systemd-resolve &>/dev/null; then
    systemd-resolve --flush-caches 2>/dev/null || true
fi
if command -v resolvectl &>/dev/null; then
    resolvectl flush-caches 2>/dev/null || true
fi

# ---- 2. TEST DNS BEFORE DOING ANYTHING ----
c_cyan "==> Testing DNS resolution..."
max_retries=5
attempt=0
dns_ok=0
while [[ $attempt -lt $max_retries ]]; do
    attempt=$((attempt + 1))
    if nslookup github.com >/dev/null 2>&1; then
        dns_ok=1
        break
    fi
    c_red "DNS test attempt $attempt/$max_retries failed. Retrying in 3s..."
    sleep 3
    # Flush cache again
    resolvectl flush-caches 2>/dev/null || true
done
if [[ $dns_ok -eq 0 ]]; then
    c_red "DNS resolution for github.com still failing after $max_retries attempts."
    c_red "Please check your network or manually set DNS."
    exit 1
fi
c_green "DNS is working."

# ---- 3. UPDATE APT & INSTALL DEPENDENCIES ----
c_cyan "==> Updating apt and installing bootstrap dependencies"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip git curl wget ca-certificates dnsutils

# ---- 4. CLEAR GIT PROXY ----
c_cyan "==> Removing any stuck Git proxy settings..."
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# ---- 5. CLONE FRESH WITH RETRIES ----
c_cyan "==> Cloning sshauto into ${APP_ROOT}"
rm -rf "${APP_ROOT}"

max_retries=5
retry_delay=5
attempt=0
clone_success=1

while [[ $attempt -lt $max_retries ]]; do
    attempt=$((attempt + 1))
    c_cyan "Clone attempt $attempt/$max_retries..."
    if git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_ROOT}"; then
        clone_success=0
        break
    fi
    c_red "Clone attempt $attempt failed. Retrying in ${retry_delay}s..."
    sleep "${retry_delay}"
    # flush DNS again before retry
    resolvectl flush-caches 2>/dev/null || true
done

if [[ $clone_success -ne 0 ]]; then
    c_red "Clone failed after ${max_retries} attempts."
    c_red "Please check your internet connection and DNS settings."
    c_red "If you have the repo locally, copy it to ${APP_ROOT} and run:"
    c_red "  sudo python3 ${APP_ROOT}/main.py install --force"
    exit 1
fi

c_green "Clone successful."

# ---- 6. SETUP PERMISSIONS & DEPENDENCIES ----
chmod +x "${APP_ROOT}/main.py"
if [[ -d "${APP_ROOT}/scripts" ]]; then
    chmod +x "${APP_ROOT}/scripts/"*.py 2>/dev/null || true
fi

c_cyan "==> Installing Python dependencies from requirements.txt"
if [[ -f "${APP_ROOT}/requirements.txt" ]]; then
    pip3 install --break-system-packages --upgrade -r "${APP_ROOT}/requirements.txt"
else
    c_red "Warning: requirements.txt not found in ${APP_ROOT}"
fi

# ---- 7. RUN INSTALLER ----
c_cyan "==> Running the automated installer (--force to rewrite all configs)"
python3 "${APP_ROOT}/main.py" install --force

c_green "==> Done. Type 'kk' any time to open the dashboard."
