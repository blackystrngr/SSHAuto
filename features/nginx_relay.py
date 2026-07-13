from pathlib import Path
from core.config import (
    APP_ROOT, PROXY_PORT_DEFAULT, HTTP_PORTS, HTTPS_PORTS,
    NGINX_SITES_AVAILABLE, NGINX_SITES_ENABLED, state
)
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

TEMPLATES_DIR = APP_ROOT / "templates"
NGINX_SITE_NAME = "ssh_tunnel"   # matches script

class NginxRelayFeature(BaseFeature):
    name = "nginx_relay"
    depends_on = ["packages", "dropbear_service", "python_proxy"]

    @property
    def available_path(self) -> Path:
        return NGINX_SITES_AVAILABLE / NGINX_SITE_NAME

    @property
    def enabled_path(self) -> Path:
        return NGINX_SITES_ENABLED / NGINX_SITE_NAME

    def is_installed(self) -> bool:
        return self.enabled_path.exists() or self.enabled_path.is_symlink()

    def install(self) -> None:
        NGINX_SITES_AVAILABLE.mkdir(parents=True, exist_ok=True)
        NGINX_SITES_ENABLED.mkdir(parents=True, exist_ok=True)

        # Remove old sshauto‑relay to avoid conflict
        old_avail = NGINX_SITES_AVAILABLE / "sshauto-relay.conf"
        old_enabled = NGINX_SITES_ENABLED / "sshauto-relay.conf"
        old_avail.unlink(missing_ok=True)
        old_enabled.unlink(missing_ok=True)

        self._disable_default_site()
        config_text = self._render()
        self.available_path.write_text(config_text)

        if not self.enabled_path.exists():
            Shell.run(f"ln -sf {self.available_path} {self.enabled_path}")

        Shell.run("nginx -t")
        Shell.run("systemctl enable nginx", check=False)
        Shell.run("systemctl reload nginx || systemctl restart nginx")
        log.success(f"nginx config written to {self.available_path}")

    def remove(self) -> None:
        Shell.run(f"rm -f {self.enabled_path}", check=False)
        Shell.run("systemctl reload nginx", check=False)

    def regenerate(self):
        self.install()

    def _disable_default_site(self):
        default = NGINX_SITES_ENABLED / "default"
        if default.exists() or default.is_symlink():
            default.unlink()
            log.debug("disabled default site")

    def _render(self) -> str:
        data = state.ensure_defaults()
        domain = data.get("cert_domain", "_")
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
        http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
        https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))

        # Use 0.0.0.0:port to match script exactly
        http_listen = "\n".join(f"    listen 0.0.0.0:{p};" for p in http_ports)

        https_block = ""
        cert_path, key_path = self._resolve_cert_paths(data)
        if cert_path and key_path:
            https_tpl = (TEMPLATES_DIR / "nginx_relay_https.conf.tpl").read_text()
            https_listen = "\n".join(f"    listen 0.0.0.0:{p} ssl;" for p in https_ports)
            https_block = (
                https_tpl
                .replace("@HTTPS_LISTEN_BLOCK@", https_listen)
                .replace("@CERT_PATH@", cert_path)
                .replace("@KEY_PATH@", key_path)
                .replace("@PROXY_PORT@", str(proxy_port))
                .replace("@DOMAIN@", domain)
            )
            log.info(f"HTTPS enabled with cert {cert_path}")
        else:
            log.warning("No certificate found – HTTPS disabled.")

        base_tpl = TEMPLATES_DIR / "nginx_relay.conf.tpl"
        if not base_tpl.exists():
            raise ConfigError(f"missing template {base_tpl}")

        return (
            base_tpl.read_text()
            .replace("@HTTP_LISTEN_BLOCK@", http_listen)
            .replace("@PROXY_PORT@", str(proxy_port))
            .replace("@DOMAIN@", domain)
            .replace("@HTTPS_SERVER_BLOCK@", https_block)
        )

    def _resolve_cert_paths(self, data):
        # Try state first
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        if cert and key and Path(cert).exists() and Path(key).exists():
            return cert, key

        # Check script's self‑signed location
        script_cert = Path("/etc/ssl/certs/selfsigned.crt")
        script_key = Path("/etc/ssl/private/selfsigned.key")
        if script_cert.exists() and script_key.exists():
            data["cert_fullchain_path"] = str(script_cert)
            data["cert_key_path"] = str(script_key)
            state.save(data)
            return str(script_cert), str(script_key)

        # Look in common locations
        from core.config import LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR
        for base in [LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, Path("/etc/ssl/cloudflare")]:
            if not base.exists():
                continue
            for f in base.glob("*/fullchain.pem"):
                k = f.parent / "privkey.pem"
                if k.exists():
                    data["cert_fullchain_path"] = str(f)
                    data["cert_key_path"] = str(k)
                    state.save(data)
                    return str(f), str(k)
        return None, None
