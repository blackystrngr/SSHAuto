#!/usr/bin/env bash
# sshauto bootstrap installer.
set -euo pipefail

REPO_URL="${SSHAUTO_REPO_URL:-https://github.com/blackystrngr/sshauto.git}"
BRANCH="${SSHAUTO_BRANCH:-main}"
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

c_cyan "==> Updating apt and installing bootstrap dependencies"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip git curl wget ca-certificates

# ---- Clear any leftover Git proxy settings ----
c_cyan "==> Removing any stuck Git proxy settings..."
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# ---- Flush DNS cache (if systemd-resolved is used) ----
if command -v systemd-resolve &>/dev/null; then
    c_cyan "Flushing DNS cache..."
    systemd-resolve --flush-caches 2>/dev/null || true
elif command -v resolvectl &>/dev/null; then
    c_cyan "Flushing DNS cache (resolvectl)..."
    resolvectl flush-caches 2>/dev/null || true
fi

# ---- Clone the repository with retries ----
c_cyan "==> Fetching sshauto into ${APP_ROOT}"

max_retries=3
retry_delay=5
attempt=0

while [[ $attempt -lt $max_retries ]]; do
    attempt=$((attempt + 1))
    c_cyan "Attempt $attempt/$max_retries..."

    # Test network connectivity to GitHub
    if ! curl -s -o /dev/null --connect-timeout 5 https://github.com; then
        c_red "Cannot reach GitHub – check your internet connection."
        if [[ $attempt -lt $max_retries ]]; then
            c_cyan "Retrying in ${retry_delay}s..."
            sleep "${retry_delay}"
            continue
        else
            c_red "Network error persists."
            exit 1
        fi
    fi

    if [[ -d "${APP_ROOT}/.git" ]]; then
        c_cyan "Repository exists – updating..."
        git -C "${APP_ROOT}" fetch origin "${BRANCH}" && git -C "${APP_ROOT}" reset --hard "origin/${BRANCH}"
        clone_success=$?
    else
        rm -rf "${APP_ROOT}"
        git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_ROOT}"
        clone_success=$?
    fi

    if [[ $clone_success -eq 0 ]]; then
        c_green "Clone successful."
        break
    fi

    if [[ $attempt -lt $max_retries ]]; then
        c_red "Clone failed. Retrying in ${retry_delay}s..."
        sleep "${retry_delay}"
    else
        c_red "Clone failed after ${max_retries} attempts."
        c_red "Please check your internet connection and DNS settings."
        c_red "If GitHub is blocked, manually clone the repo and run:"
        c_red "  sudo python3 ${APP_ROOT}/main.py install --force"
        exit 1
    fi
done

# ---- Post-clone setup ----
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

c_cyan "==> Running the automated installer (--force to rewrite all configs)"
python3 "${APP_ROOT}/main.py" install --force

c_green "==> Done. Type 'kk' any time to open the dashboard."
