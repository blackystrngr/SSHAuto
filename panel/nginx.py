"""nginx snippet that publishes the panel on a secret path of the relay's own server blocks."""
from __future__ import annotations


def render_panel_location(port, path) -> str:
    if not port or not path:
        return ""
    return f"""
    location = /{path} {{ return 301 /{path}/; }}
    location ^~ /{path}/ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_read_timeout 120s;
        client_max_body_size 64k;
    }}
"""
