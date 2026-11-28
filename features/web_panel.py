from __future__ import annotations

import shutil
import socket
import time
import urllib.request

from core.config import APP_ROOT, SYSTEMD_DIR, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

UNIT = "sshauto-panel"
UNIT_PATH = SYSTEMD_DIR / f"{UNIT}.service"
PANEL_KEYS = ("panel_port", "panel_path", "panel_user", "panel_hash", "panel_secret")


def render_unit(python: str) -> str:
    return f"""[Unit]
Description=sshauto web panel (loopback only, published by the relay)
After=network.target

[Service]
ExecStart={python} {APP_ROOT}/main.py serve-panel
Restart=always
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
"""


def _free_port(start: int = 8088) -> int:
    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free loopback port for the panel")


def panel_url(data: dict) -> str | None:
    if not data.get("panel_path"):
        return None
    return f"https://{data.get('cert_domain') or '<your-domain>'}/{data['panel_path']}/"


def local_ok(port: int, path: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}/", timeout=timeout) as resp:
            return resp.status == 200
    except OSError:
        return False


class WebPanelFeature(BaseFeature):
    name = "web_panel"
    description = "Web panel (optional): monitor and manage everything from a browser"
    depends_on = ["packages", "python_proxy"]
    optional = True
    priority = 100   # always installed last

    def is_installed(self) -> bool:
        return UNIT_PATH.exists() and Shell.run(f"systemctl is-active {UNIT}", check=False, timeout=10).ok

    def install(self) -> None:
        from panel.api import new_secret_path
        from panel.auth import Auth, generate_password

        data = state.ensure_defaults()
        data["panel_port"] = data.get("panel_port") or _free_port()
        data["panel_path"] = data.get("panel_path") or new_secret_path()
        data["panel_user"] = data.get("panel_user") or "admin"
        state.save(data)

        auth = Auth()
        if not auth.configured():
            password = generate_password()
            auth.set_credentials(data["panel_user"], password)
            log.important(f"Panel login: {data['panel_user']} / {password}   (shown once; reset with: main.py panel --reset-password)")

        python = shutil.which("python3") or "/usr/bin/python3"
        UNIT_PATH.write_text(render_unit(python))
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        Shell.run(f"systemctl enable {UNIT}", check=False, timeout=10)
        Shell.run(f"systemctl restart {UNIT}", check=False, timeout=30)

        port, path = int(data["panel_port"]), data["panel_path"]
        for _ in range(10):
            if local_ok(port, path):
                break
            time.sleep(1)
        else:
            raise Exception(f"panel did not start (journalctl -u {UNIT})")

        log.important(f"Panel URL: {panel_url(state.load())}")
        log.info(f"Or through an SSH tunnel: ssh -L {port}:127.0.0.1:{port} root@<server>  then  http://127.0.0.1:{port}/{path}/")

    def remove(self) -> None:
        Shell.run(f"systemctl disable --now {UNIT}", check=False, timeout=20)
        UNIT_PATH.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        data = state.load()
        for key in PANEL_KEYS:
            data.pop(key, None)
        state.save(data)
        log.info("Web panel removed.")
