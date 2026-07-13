import os
from pathlib import Path
from core.shell import Shell
from core.logger import log
from core.config import state
from features.base import BaseFeature

class NginxRelayFeature(BaseFeature):
    name = "nginx_relay"
    description = "Nginx Front-End HTTPS TLS Termination & WebSocket Router"
    # Depends on packages being installed and certificates existing
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return os.path.exists("/etc/nginx/sites-enabled/sshauto-relay") and \
               Shell.run("systemctl is-active nginx", check=False).ok

    def install(self) -> None:
        log.info("Configuring Nginx reverse proxy routing rules...")

        # Ensure standard default landing pages don't conflict with our setup
        Shell.run("rm -f /etc/nginx/sites-enabled/default", check=False)

        # Retrieve the domain from state store or fallback gracefully
        domain = state.get("cert_domain", "localhost")
        
        # Absolute paths where the Certificate Provisioner saves files
        cert_path = "/var/lib/sshauto/certs/server.crt"
        key_path = "/var/lib/sshauto/certs/server.key"

        # Safe fallback validation: Create a dummy path if user hasn't run the cert wizard yet
        # so Nginx won't throw a fatal parsing crash during startup configuration checks.
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            log.warning("Active SSL certificates not detected yet. Provisioning placeholder self-signed keys...")
            Path("/var/lib/sshauto/certs").mkdir(parents=True, exist_ok=True)
            Shell.run(
                f"openssl req -x509 -nodes -days 7 -newkey rsa:2048 "
                f"-keyout {key_path} -out {cert_path} -subj '/CN=localhost'",
                check=True
            )

        # Define pristine Nginx Virtual Host configuration payload
        nginx_config = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    
    # Enforce global redirection from HTTP to HTTPS
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain};

    # Cloudflare Origin CA Cryptographic Key Pairs
    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};

    # Hardened production SSL parameters
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # Core Tunnel Gateway Endpoint
    location / {{
        proxy_pass http://127.0.0.1:8000;
        
        # Absolute requirement for stable HTTP Upgrade streams
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        
        # Pass downstream origin identifying headers directly to proxy layer
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffers to ensure completely real-time, low-latency SSH interaction
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
}}
"""

        # Write out configuration safely
        config_path = "/etc/nginx/sites-available/sshauto-relay"
        with open(config_path, "w") as f:
            f.write(nginx_config)

        # Enable configuration link
        enabled_link = "/etc/nginx/sites-enabled/sshauto-relay"
        if not os.path.exists(enabled_link):
            os.symlink(config_path, enabled_link)

        # Validate syntax cleanly before reloading daemon processes
        log.info("Testing Nginx configuration structural integrity...")
        test_res = Shell.run("nginx -t", check=False)
        if not test_res.ok:
            raise Exception(f"Nginx configuration verification rejected: {test_res.stderr}")

        # Apply changes live
        Shell.run("systemctl daemon-reload", check=False)
        Shell.run("systemctl enable nginx", check=False)
        Shell.run("systemctl restart nginx", check=True)
        
        log.success("Nginx structural reverse-proxy rules successfully deployed and active.")

    def remove(self) -> None:
        Shell.run("rm -f /etc/nginx/sites-enabled/sshauto-relay", check=False)
        Shell.run("rm -f /etc/nginx/sites-available/sshauto-relay", check=False)
        Shell.run("systemctl restart nginx", check=False)
        log.info("Nginx routing features removed.")
