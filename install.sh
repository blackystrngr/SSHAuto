#!/usr/bin/env bash
# sshauto bootstrap installer.
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

# ---- 1. FULL IPTABLES FLUSH ----
c_cyan "==> Flushing all iptables rules (filter, nat, mangle, raw)..."

rm -rf /etc/iptables/rules.v6 /etc/iptables/rules.v4 /usr/share/netfilter-persistent/plugins.d/15-ip4tables /usr/share/netfilter-persistent/plugins.d/25-ip6tables
sudo iptables -F

sudo iptables -P INPUT ACCEPT
sudo iptables -P FORWARD ACCEPT
sudo iptables -P OUTPUT ACCEPT

sudo iptables-save > /etc/iptables/rules.v4
sudo iptables-save > /etc/iptables/rules.v6
apt install netfilter-persistent -y
sudo netfilter-persistent save

c_green "iptables flushed and default policies set to ACCEPT."


# ---- 4. CLEAR GIT PROXY ----
c_cyan "==> Removing any stuck Git proxy settings..."
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true
# ---- 2. FIX SYSTEMD-RESOLVED CONFIGURATION (DNS: 1.1.1.1, 1.0.0.1) ----

# ---- 3. INSTALL DEPENDENCIES ----
c_cyan "==> Updating apt and installing bootstrap dependencies"
apt-get update -y
apt-get install -y python3 python3-pip git curl wget ca-certificates



# ---- 5. CLONE WITH RETRIES ----
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
done

if [[ $clone_success -ne 0 ]]; then
    c_red "Clone failed after ${max_retries} attempts."
    c_red "Please check your internet connection."
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
