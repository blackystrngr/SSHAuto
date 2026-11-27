import socket
import time
from pathlib import Path

from core.config import APP_ROOT, HTTP_PORTS, HTTPS_PORTS, LOG_DIR, PROXY_PORT_DEFAULT, state
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

PROXY_BIN = Path("/usr/local/bin/ws_ssh_proxy.py")
RELAY_SOURCE = APP_ROOT / "relay" / "ws_ssh_proxy.py"
SERVICE_NAME = "ws-ssh-proxy.service"
SERVICE_PATH = Path(f"/etc/systemd/system/{SERVICE_NAME}")

UNIT = """[Unit]
Description=sshauto relay (TLS + WebSocket + CONNECT + raw SSH + web panel, direct public bind)
After=network-online.target dropbear-tunnel.service netfilter-persistent.service iptables.service
Wants=dropbear-tunnel.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {bin}
Restart=always
RestartSec=2
User=root
LimitNOFILE=1048576
TasksMax=infinity

[Install]
WantedBy=multi-user.target
"""


class PythonProxyFeature(BaseFeature):
    """The relay is the only public listener. It binds every HTTP/HTTPS relay port itself, terminates
    TLS itself and serves every protocol on every port (see relay/ws_ssh_proxy.py). There is no
    reverse proxy in front of it."""
    name = "python_proxy"
    description = "Relay: TLS + WebSocket + CONNECT + raw SSH on every relay port"
    depends_on = ["packages", "dropbear_service", "certificates", "firewall"]

    def is_installed(self) -> bool:
        return PROXY_BIN.exists() and Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False).ok

    def install(self) -> None:
        data = state.ensure_defaults()
        if not RELAY_SOURCE.exists():
            raise Exception(f"relay source missing: {RELAY_SOURCE}")
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._ensure_uvloop()
        self._retire_nginx()
        self._free_loopback_port(data)

        PROXY_BIN.write_text(RELAY_SOURCE.read_text())
        PROXY_BIN.chmod(0o755)
        SERVICE_PATH.write_text(UNIT.format(bin=PROXY_BIN))

        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        Shell.run(f"systemctl enable {SERVICE_NAME}", check=False, timeout=10)
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=15)
        Shell.run(f"systemctl reset-failed {SERVICE_NAME}", check=False, timeout=10)
        result = Shell.run(f"systemctl start {SERVICE_NAME}", check=False, timeout=30)
        if not result.ok:
            log.error(f"Relay start failed: {result.stderr}")
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 20", check=False)
            raise Exception("Relay failed to start")

        port = state.load().get("proxy_port", PROXY_PORT_DEFAULT)
        for _ in range(20):                       # wait until it really accepts connections
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            Shell.run(f"journalctl -u {SERVICE_NAME} --no-pager -n 20", check=False)
            raise Exception("Relay is not accepting connections")

        if not data.get("cert_fullchain_path"):
            log.important("No certificate yet: TLS clients are refused until you run 'main.py cert'.")
        http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
        https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))
        log.success(f"Relay listening on {len(http_ports) + len(https_ports)} public ports "
                    f"(TLS or plain on any of them); loopback :{port}")

    def remove(self) -> None:
        Shell.run(f"systemctl stop {SERVICE_NAME}", check=False, timeout=15)
        Shell.run(f"systemctl disable {SERVICE_NAME}", check=False, timeout=10)
        SERVICE_PATH.unlink(missing_ok=True)
        PROXY_BIN.unlink(missing_ok=True)
        Shell.run("systemctl daemon-reload", check=False, timeout=10)
        log.info("Relay removed")

    def restart(self) -> bool:
        """Needed only when the set of listening ports changes (certificate, panel and Dropbear
        settings are re-read live)."""
        return Shell.run(f"systemctl restart {SERVICE_NAME}", check=False, timeout=20).ok

    def _retire_nginx(self) -> None:
        """Older versions put nginx in front of the proxy. It would hold ports 80/443, so switch
        it off and delete our site file. The package itself is left alone."""
        site = Path("/etc/nginx/sites-enabled/sshauto-relay.conf")
        avail = Path("/etc/nginx/sites-available/sshauto-relay.conf")
        had_site = site.exists() or site.is_symlink() or avail.exists()
        if had_site:
            site.unlink(missing_ok=True)
            avail.unlink(missing_ok=True)
        if Shell.run("systemctl is-active nginx", check=False, timeout=10).ok and \
                (had_site or Shell.run("ss -ltn", check=False, timeout=10).stdout.count(":80 ")):
            log.warning("Stopping nginx: the relay binds the public ports itself.")
            Shell.run("systemctl disable --now nginx", check=False, timeout=20)

    def _free_loopback_port(self, data: dict) -> None:
        port = int(data.get("proxy_port", PROXY_PORT_DEFAULT) or PROXY_PORT_DEFAULT)
        if port in (8000, 8001) or not self._port_available(port):
            if Shell.run(f"systemctl is-active {SERVICE_NAME}", check=False, timeout=10).ok:
                return                            # our own running relay holds it
            for cand in range(PROXY_PORT_DEFAULT, PROXY_PORT_DEFAULT + 50):
                if self._port_available(cand):
                    state.set("proxy_port", cand)
                    return

    def _ensure_uvloop(self) -> None:
        """uvloop is mandatory: try pip (new + old pip syntax), then apt."""
        def have() -> bool:
            return Shell.run("python3 -c 'import uvloop'", check=False, timeout=20).ok
        if have():
            return
        log.info("Installing uvloop...")
        for cmd in ("pip3 install -q uvloop --break-system-packages",
                    "pip3 install -q uvloop",
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-uvloop"):
            Shell.run(cmd, check=False, timeout=300)
            if have():
                log.success("uvloop installed.")
                return
        raise Exception("uvloop could not be installed (tried pip and apt)")

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False
