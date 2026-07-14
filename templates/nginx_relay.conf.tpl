# ============================================================
#  Managed by sshauto – split traffic: WebSocket → Python proxy,
#  plain HTTP → Squid proxy.
# ============================================================

upstream python_backend {
    server 127.0.0.1:@PROXY_PORT@;
}

upstream squid_backend {
    server 127.0.0.1:3128;
}

server {
@HTTP_LISTEN_BLOCK@
    server_name @DOMAIN@;
    tcp_nodelay on;

    client_header_timeout 86400s;
    client_body_timeout 86400s;
    client_max_body_size 0;

    location / {
        # If Upgrade: websocket, go to Python proxy, else to Squid
        if ($http_upgrade = "websocket") {
            proxy_pass http://python_backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            break;
        }
        proxy_pass http://squid_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    # Common settings for both backends
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_connect_timeout 10s;
}

@HTTPS_SERVER_BLOCK@
