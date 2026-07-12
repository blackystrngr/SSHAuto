# ============================================================
# Managed by sshauto — Remade for aggressive fake 101 tunneling
# ============================================================

# Listen on many common ports used by injectors
server {
    # HTTP ports
    listen 80 default_server reuseport;
    listen 8080 default_server reuseport;
    listen 8880 default_server reuseport;

    # HTTPS ports
    listen 443 ssl default_server reuseport;
    listen 8443 ssl default_server reuseport;
    listen 2083 ssl default_server reuseport;
    listen 2087 ssl default_server reuseport;
    listen 2053 ssl default_server reuseport;
    listen 2096 ssl default_server reuseport;

    server_name _;        # Catch all domains / SNI
    http2 on;

    # SSL Configuration (change yourdomain.com to your actual domain)
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # ==================== MAIN TUNNEL LOCATION ====================
    location / {
        proxy_pass http://127.0.0.1:109;     # Your Dropbear port

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Critical for long-lived tunneling
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_connect_timeout 10s;

        # Force Fake 101 for ALL connections (what you wanted)
        proxy_intercept_errors on;
        error_page 502 503 504 400 403 =101 @fake_upgrade;
    }

    # Fake 101 Response
    location @fake_upgrade {
        return 101 "HTTP/1.1 101 Switching Protocols\r\n\r\n";
    }
}

# Upgrade map (kept for compatibility)
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
