# ---- TLS relay ---------------------------------------------------------
# TLS is terminated here (self-signed / ACME / Cloudflare Origin CA -
# whichever the operator picked at install time), then the decrypted
# websocket-upgrade request is forwarded to dropbear exactly like the
# plain-HTTP block above. From the client's perspective this is a normal
# HTTPS connection to a CDN edge; the CDN/edge sees only encrypted bytes.
server {
@HTTPS_LISTEN_BLOCK@
    server_name _;

    ssl_certificate     @CERT_PATH@;
    ssl_certificate_key @KEY_PATH@;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    location / {
        proxy_pass http://127.0.0.1:@PROXY_PORT@;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
