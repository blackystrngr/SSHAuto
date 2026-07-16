#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/blackystrngr/SSHAuto"
BRANCH="main"
APP_ROOT="/opt/sshauto"

c_red()   { printf '\033[31m%s\033[0m\n' "$1"; }
c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_cyan()  { printf '\033[36m%s\033[0m\n' "$1"; }

if [[ $EUID -ne 0 ]]; then
    c_red "Must run as root"
    exit 1
fi

if ! grep -qiE 'debian|ubuntu' /etc/os-release; then
    c_red "Only Ubuntu/Debian supported"
    exit 1
fi

c_cyan "==> Installing dependencies"
apt-get update -y
apt-get install -y python3 python3-pip git curl wget ca-certificates

c_cyan "==> Removing git proxy settings"
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

c_cyan "==> Cloning repository"
rm -rf "${APP_ROOT}"
git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_ROOT}"

chmod +x "${APP_ROOT}/main.py"

c_cyan "==> Installing Python dependencies"
pip3 install --break-system-packages --upgrade -r "${APP_ROOT}/requirements.txt"

c_cyan "==> Running installer"
python3 "${APP_ROOT}/main.py" install --force

c_green "Done. Type 'kk' to open dashboard."
