map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
@HTTP_LISTEN_BLOCK@
    server_name @DOMAIN@;
    tcp_nodelay on;
    access_log off;

    client_header_timeout 86400s;
    client_body_timeout 86400s;
    client_max_body_size 0;

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
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_connect_timeout 10s;
        tcp_nodelay on;
    }
}

@HTTPS_SERVER_BLOCK@
