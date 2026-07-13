# ============================================================
#  Managed by sshauto – optimised for speed
# ============================================================

server {
@HTTP_LISTEN_BLOCK@
    server_name @DOMAIN@;
    tcp_nodelay on;

    client_header_timeout 86400s;
    client_body_timeout 86400s;
    client_max_body_size 0;

    # Buffer tuning for large file transfers
    proxy_buffer_size 128k;
    proxy_buffers 4 256k;
    proxy_busy_buffers_size 256k;

    location / {
        proxy_pass http://127.0.0.1:@PROXY_PORT@;
        proxy_http_version 1.1;
        proxy_set_header Host $host;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_connect_timeout 10s;
    }
}

@HTTPS_SERVER_BLOCK@
