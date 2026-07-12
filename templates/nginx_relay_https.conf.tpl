# ---- TLS relay (HTTPS) -------------------------------------------------
# TLS is terminated here, then we immediately return fake 101 and forward
# raw bytes to Dropbear. Works with CDN fronting.

server {
    @HTTPS_LISTEN_BLOCK@

    server_name _;                    # Catch-all (any domain/SNI)

    ssl_certificate @CERT_PATH@;
    ssl_certificate_key @KEY_PATH@;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # ==================== MAIN TUNNEL LOCATION ====================
    location / {
        proxy_pass http://127.0.0.1:@DROPBEAR_PORT@;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Critical settings for long-lived raw tunneling
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_connect_timeout 10s;

        # === Force Fake 101 for every connection ===
        proxy_intercept_errors on;
        error_page 502 503 504 400 403 502 =101 @fake_upgrade;
    }

    # Fake 101 Response Location
    location @fake_upgrade {
        return 101 "HTTP/1.1 101 Switching Protocols\r\n\r\n";
    }
}

# Upgrade map (needed for real websocket clients)
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
