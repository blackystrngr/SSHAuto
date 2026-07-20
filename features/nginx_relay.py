from pathlib import Path
from core.config import (
    APP_ROOT, PROXY_PORT_DEFAULT, HTTP_PORTS, HTTPS_PORTS,
    NGINX_SITES_AVAILABLE, NGINX_SITES_ENABLED, state,
    LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR
)
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

TEMPLATES_DIR = APP_ROOT / "templates"
NGINX_SITE_NAME = "sshauto-relay"

class NginxRelayFeature(BaseFeature):
    name = "nginx_relay"
    description = "Generate the nginx WebSocket relay (HTTP+HTTPS)"
    depends_on = ["packages", "dropbear_service", "python_proxy", "certificates"]

    @property
    def available_path(self):
        return NGINX_SITES_AVAILABLE / f"{NGINX_SITE_NAME}.conf"
    @property
    def enabled_path(self):
        return NGINX_SITES_ENABLED / f"{NGINX_SITE_NAME}.conf"

    def is_installed(self) -> bool:
        return self.enabled_path.exists() or self.enabled_path.is_symlink()

    def install(self) -> None:
        NGINX_SITES_AVAILABLE.mkdir(parents=True, exist_ok=True)
        NGINX_SITES_ENABLED.mkdir(parents=True, exist_ok=True)
        self._remove_conflicting_sites()
        self._disable_default_site()
        config_text = self._render()
        self.available_path.write_text(config_text)
        if not self.enabled_path.exists():
            Shell.run(f"ln -sf {self.available_path} {self.enabled_path}")
        Shell.run("nginx -t")
        Shell.run("systemctl enable nginx", check=False)
        if not Shell.run("systemctl reload nginx", check=False, timeout=10).ok:
            log.warning("nginx reload failed; restarting")
            Shell.run("systemctl restart nginx", check=False, timeout=10)
        log.success(f"nginx WebSocket relay written to {self.available_path}")

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

    def _remove_conflicting_sites(self):
        keep = [f"{NGINX_SITE_NAME}.conf", "default"]
        for site in NGINX_SITES_ENABLED.glob("*.conf"):
            if site.name not in keep:
                log.info(f"Removing conflicting nginx site: {site.name}")
                site.unlink()
        for site in NGINX_SITES_ENABLED.glob("*"):
            if site.is_symlink() and site.name not in keep:
                try:
                    target = site.readlink()
                    if target.name != f"{NGINX_SITE_NAME}.conf":
                        log.info(f"Removing conflicting symlink: {site.name}")
                        site.unlink()
                except:
                    pass

    def _render(self) -> str:
        data = state.ensure_defaults()
        domain = data.get("cert_domain", "_")
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
        http_ports = sorted(HTTP_PORTS | set(data.get("custom_http_ports", [])))
        https_ports = sorted(HTTPS_PORTS | set(data.get("custom_https_ports", [])))

        # Remove 443 if sslh is installed (but we removed sslh, so this is just safe)
        if Path("/etc/sslh.cfg").exists() or Path("/etc/sslh/sslh.conf").exists():
            if 443 in https_ports:
                https_ports.remove(443)
                log.debug("sslh detected – nginx will not listen on 443.")

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
            log.info(f"HTTPS block enabled with cert {cert_path}")
        else:
            log.critical("No valid certificate found. Run 'sshauto cert' first.")
            raise ConfigError("Certificate missing.")

        base_tpl = TEMPLATES_DIR / "nginx_relay.conf.tpl"
        return (
            base_tpl.read_text()
            .replace("@HTTP_LISTEN_BLOCK@", http_listen)
            .replace("@PROXY_PORT@", str(proxy_port))
            .replace("@DOMAIN@", domain)
            .replace("@HTTPS_SERVER_BLOCK@", https_block)
        )

    def _resolve_cert_paths(self, data):
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        if cert and key and Path(cert).exists() and Path(key).exists():
            return cert, key
        domain = data.get("cert_domain")
        if domain:
            # Cloudflare / self-signed from SSHAUTO_CERT_DIR
            cf_cert = SSHAUTO_CERT_DIR / domain / "fullchain.pem"
            cf_key = SSHAUTO_CERT_DIR / domain / "privkey.pem"
            if cf_cert.exists() and cf_key.exists():
                data["cert_fullchain_path"] = str(cf_cert)
                data["cert_key_path"] = str(cf_key)
                state.save(data)
                return str(cf_cert), str(cf_key)
            # Let's Encrypt
            le_cert = LETSENCRYPT_LIVE / domain / "fullchain.pem"
            le_key = LETSENCRYPT_LIVE / domain / "privkey.pem"
            if le_cert.exists() and le_key.exists():
                data["cert_fullchain_path"] = str(le_cert)
                data["cert_key_path"] = str(le_key)
                state.save(data)
                return str(le_cert), str(le_key)
        # Fallback self-signed
        script_cert = Path("/etc/ssl/certs/selfsigned.crt")
        script_key = Path("/etc/ssl/private/selfsigned.key")
        if script_cert.exists() and script_key.exists():
            data["cert_fullchain_path"] = str(script_cert)
            data["cert_key_path"] = str(script_key)
            state.save(data)
            return str(script_cert), str(script_key)
        return None, None
