"""
Builds /etc/nginx/sites-available/sshauto-relay.conf from the two
templates and symlinks it into sites-enabled. Regenerated any time ports
change (dashboard add/remove-port) or the cert changes.
"""
from __future__ import annotations

from pathlib import Path

from core.config import (
    APP_ROOT,
    DROPBEAR_PORT_DEFAULT,
    HTTP_PORTS,
    HTTPS_PORTS,
    NGINX_RELAY_NAME,
    NGINX_SITES_AVAILABLE,
    NGINX_SITES_ENABLED,
    state,
)
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

TEMPLATES_DIR = APP_ROOT / "templates"


class NginxRelayFeature(BaseFeature):
    name = "nginx_relay"
    description = "Generate the nginx websocket relay (HTTP+HTTPS -> dropbear)"
    depends_on = ["packages", "dropbear_service"]

    @property
    def available_path(self) -> Path:
        return NGINX_SITES_AVAILABLE / f"{NGINX_RELAY_NAME}.conf"

    @property
    def enabled_path(self) -> Path:
        return NGINX_SITES_ENABLED / f"{NGINX_RELAY_NAME}.conf"

    def is_installed(self) -> bool:
        return self.enabled_path.exists() or self.enabled_path.is_symlink()

    def install(self) -> None:
        NGINX_SITES_AVAILABLE.mkdir(parents=True, exist_ok=True)
        NGINX_SITES_ENABLED.mkdir(parents=True, exist_ok=True)

        self._disable_default_site()
        config_text = self._render()
        self.available_path.write_text(config_text)

        if not self.enabled_path.exists():
            Shell.run(f"ln -sf {self.available_path} {self.enabled_path}")

        Shell.run("nginx -t")  # validate before touching the live service
        Shell.run("systemctl enable nginx", check=False)
        Shell.run("systemctl reload nginx || systemctl restart nginx")
        log.success(f"nginx relay written to {self.available_path} and reloaded")

    def remove(self) -> None:
        Shell.run(f"rm -f {self.enabled_path}", check=False)
        Shell.run("systemctl reload nginx", check=False)

    def regenerate(self):
        """Called by the dashboard after ports/cert change."""
        self.install()

    # -- rendering ------------------------------------------------------
    def _disable_default_site(self):
        default_link = NGINX_SITES_ENABLED / "default"
        if default_link.exists() or default_link.is_symlink():
            default_link.unlink()
            log.debug("disabled nginx's default site (would shadow our catch-all)")

    def _render(self) -> str:
        data = state.ensure_defaults()
        dropbear_port = data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)
        proxy_port = data.get("websocket_proxy_port", 109)  # from proxy feature
        http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
        https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))

        http_listen_block = "\n".join(f"    listen {p};" for p in http_ports)

        https_server_block = ""
        cert_path, key_path = self._resolve_cert_paths(data)
        if cert_path and key_path:
            https_tpl = (TEMPLATES_DIR / "nginx_relay_https.conf.tpl").read_text()
            https_listen_block = "\n".join(f"    listen {p} ssl;" for p in https_ports)
            https_server_block = (
                https_tpl
                .replace("@HTTPS_LISTEN_BLOCK@", https_listen_block)
                .replace("@CERT_PATH@", cert_path)
                .replace("@KEY_PATH@", key_path)
                .replace("@PROXY_PORT@", str(proxy_port))
            )
        else:
            log.warning("no certificate configured yet — HTTPS relay ports "
                        "will not be enabled until `sshauto cert` runs")

        base_tpl_path = TEMPLATES_DIR / "nginx_relay.conf.tpl"
        if not base_tpl_path.exists():
            raise ConfigError(f"missing template {base_tpl_path}")

        return (
            base_tpl_path.read_text()
            .replace("@HTTP_LISTEN_BLOCK@", http_listen_block)
            .replace("@DROPBEAR_PORT@", str(dropbear_port))
            .replace("@HTTPS_SERVER_BLOCK@", https_server_block)
        )

    def _resolve_cert_paths(self, data: dict) -> tuple[str | None, str | None]:
        cert_path = data.get("cert_fullchain_path")
        key_path = data.get("cert_key_path")
        if cert_path and key_path and Path(cert_path).exists() and Path(key_path).exists():
            return cert_path, key_path
        return None, None
