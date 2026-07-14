# ... existing content above ...

# Network layout
HTTP_PORTS = {80, 8080, 8880, 2052, 2082, 2086, 2095}
HTTPS_PORTS = {443, 8443, 2053, 2083, 2087, 2096}   # 443 is handled by sslh if installed

SSH_PORT_DEFAULT = 22
DROPBEAR_PORT_DEFAULT = 110
PROXY_PORT_DEFAULT = 8000
SQUID_PORT_DEFAULT = 3128
STUNNEL_PORT_DEFAULT = 4443

USER_GROUP = "sshauto-users"
GIT_POLL_INTERVAL_SECONDS = 30

# Package Management
REQUIRED_PACKAGES = ["nginx", "dropbear", "fail2ban", "iptables", "curl", "git", "certbot", "squid", "stunnel4", "sslh"]
REMOVE_PACKAGES = ["apache2", "ufw", "firewalld"]
PIP_PACKAGES = []

# ... rest of file unchanged ...
