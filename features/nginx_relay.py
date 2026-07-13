"""
Builds /etc/nginx/sites-available/sshauto-relay.conf from the two
templates and symlinks it into sites-enabled. Regenerated any time ports
change (dashboard add/remove-port) or the cert changes.

The templates no longer handle the WebSocket Upgrade header; the Python
proxy does that independently. This gives us lower latency and simpler
nginx configuration.
"""
from __future__ import annotations

from pathlib import Path

from core.config import (
    APP_ROOT,
    DROPBEAR_PORT_DEFAULT,
    PROXY_PORT_DEFAULT,
    HTTP_PORTS,
    HTTPS_PORTS,
    NGINX_RELAY_NAME,
    NGINX_SITES_AVAILABLE,
    NGINX_SITES_ENABLED,
    LETSENCRYPT_LIVE,
    SSHAUTO_CERT_DIR,
    state,
)
from core.exceptions import ConfigError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature

TEMPLATES_DIR = APP_ROOT / "templates"

# Common locations where SSL certificates might be stored
CERT_SEARCH_PATHS = [
    LETSENCRYPT_LIVE,          # /etc/letsencrypt/live/
    SSHAUTO_CERT_DIR,          # /var/lib/sshauto/certs/
    Path("/etc/ssl/cloudflare"),
    Path("/etc/ssl/certs"),
]

# Common certificate filename pairs to look for (cert, key)
CERT_PAIRS = [
    ("fullchain.pem", "privkey.pem"),
    ("cert.pem", "key.pem"),
    ("server.crt", "server.key"),
    ("domain.crt", "domain.key"),
    ("fullchain.crt", "privkey.key"),
]


class NginxRelayFeature(BaseFeature):
    name = "nginx_relay"
    description = "Generate the nginx websocket relay (HTTP+HTTPS -> dropbear)"
    depends_on = ["packages", "dropbear_service", "python_proxy"]

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

        Shell.run("nginx -t")
        Shell.run("systemctl enable nginx", check=False)
        Shell.run("systemctl reload nginx || systemctl restart nginx")
        log.success(f"nginx relay written to {self.available_path} and reloaded")

    def remove(self) -> None:
        Shell.run(f"rm -f {self.enabled_path}", check=False)
        Shell.run("systemctl reload nginx", check=False)

    def regenerate(self):
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
        proxy_port = data.get("proxy_port", PROXY_PORT_DEFAULT)
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
            log.info(f"HTTPS block enabled with certificate: {cert_path}")
        else:
            log.warning("No certificate found – HTTPS relay ports will not be enabled. "
                        "Run 'sshauto cert' or place certificate in one of: "
                        f"{', '.join(str(p) for p in CERT_SEARCH_PATHS)}")

        base_tpl_path = TEMPLATES_DIR / "nginx_relay.conf.tpl"
        if not base_tpl_path.exists():
            raise ConfigError(f"missing template {base_tpl_path}")

        return (
            base_tpl_path.read_text()
            .replace("@HTTP_LISTEN_BLOCK@", http_listen_block)
            .replace("@PROXY_PORT@", str(proxy_port))
            .replace("@HTTPS_SERVER_BLOCK@", https_server_block)
        )

    def _resolve_cert_paths(self, data: dict) -> tuple[str | None, str | None]:
        """
        Resolve certificate and key paths.
        Tries:
        1. Paths stored in state.
        2. Auto‑discover from domain using multiple standard locations and common filename pairs.
        If found, updates state and returns paths.
        """
        # 1. Check state
        cert_path = data.get("cert_fullchain_path")
        key_path = data.get("cert_key_path")
        if cert_path and key_path and Path(cert_path).exists() and Path(key_path).exists():
            log.debug(f"Using certificate from state: {cert_path}")
            return cert_path, key_path

        # 2. Auto‑discover using domain
        domain = data.get("cert_domain")
        if domain:
            # Search in each base directory
            for base_dir in CERT_SEARCH_PATHS:
                if not base_dir.exists():
                    continue
                # Try exact domain subdirectory
                domain_dir = base_dir / domain
                if domain_dir.exists():
                    for cert_name, key_name in CERT_PAIRS:
                        cert = domain_dir / cert_name
                        key = domain_dir / key_name
                        if cert.exists() and key.exists():
                            log.info(f"Auto‑discovered certificate pair in {domain_dir}")
                            data["cert_fullchain_path"] = str(cert)
                            data["cert_key_path"] = str(key)
                            state.save(data)
                            return str(cert), str(key)

                # For /etc/ssl/certs, files may be named <domain>.crt or <domain>.pem
                if base_dir == Path("/etc/ssl/certs"):
                    for f in base_dir.glob(f"{domain}.*"):
                        if f.is_file() and f.suffix in (".crt", ".pem"):
                            # Try to find corresponding key in /etc/ssl/private/
                            key_name = f.stem + ".key"
                            key_candidate = Path("/etc/ssl/private") / key_name
                            if key_candidate.exists():
                                log.info(f"Auto‑discovered certificate: {f} and key: {key_candidate}")
                                data["cert_fullchain_path"] = str(f)
                                data["cert_key_path"] = str(key_candidate)
                                state.save(data)
                                return str(f), str(key_candidate)

        # 3. Last resort: look for any common certificate pairs in the base directories
        for base_dir in CERT_SEARCH_PATHS:
            if not base_dir.exists():
                continue
            for cert_name, key_name in CERT_PAIRS:
                cert = base_dir / cert_name
                key = base_dir / key_name
                if cert.exists() and key.exists():
                    log.info(f"Found certificate pair: {cert} and {key}")
                    # Try to extract domain from parent directory name
                    domain = cert.parent.name if cert.parent.name != "certs" else None
                    data["cert_domain"] = domain or "unknown"
                    data["cert_fullchain_path"] = str(cert)
                    data["cert_key_path"] = str(key)
                    state.save(data)
                    return str(cert), str(key)

        # 4. If still nothing, try to find any .crt + .key in SSHAUTO_CERT_DIR (where server.crt/server.key lives)
        if SSHAUTO_CERT_DIR.exists():
            for cert in SSHAUTO_CERT_DIR.glob("*.crt"):
                key = cert.with_suffix(".key")
                if key.exists():
                    log.info(f"Found certificate pair: {cert} and {key}")
                    data["cert_fullchain_path"] = str(cert)
                    data["cert_key_path"] = str(key)
                    state.save(data)
                    return str(cert), str(key)
            for cert in SSHAUTO_CERT_DIR.glob("*.pem"):
                key = cert.with_suffix(".key")
                if key.exists():
                    log.info(f"Found certificate pair: {cert} and {key}")
                    data["cert_fullchain_path"] = str(cert)
                    data["cert_key_path"] = str(key)
                    state.save(data)
                    return str(cert), str(key)

        # Nothing found
        return None, None
