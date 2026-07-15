#!/usr/bin/env bash
# sshauto bootstrap installer.
# Usage on a fresh Debian/Ubuntu VPS:
#   curl -fsSL https://raw.githubusercontent.com/<you>/sshauto/main/install.sh | sudo bash
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

c_cyan "==> Fetching sshauto into ${APP_ROOT}"
if [[ -d "${APP_ROOT}/.git" ]]; then
    git -C "${APP_ROOT}" fetch origin "${BRANCH}"
    git -C "${APP_ROOT}" reset --hard "origin/${BRANCH}"
else
    rm -rf "${APP_ROOT}"
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_ROOT}"
fi

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
