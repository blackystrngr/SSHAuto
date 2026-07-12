"""
Automated non-interactive ACME Account allocation and generation layer.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from core.config import LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, state
from core.exceptions import CertificateError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class CertStrategy:
    def issue(self, domain: str) -> tuple[str, str]:
        raise NotImplementedError


class SelfSignedStrategy(CertStrategy):
    def issue(self, domain: str) -> tuple[str, str]:
        out_dir = SSHAUTO_CERT_DIR / domain
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_path = out_dir / "fullchain.pem"
        key_path = out_dir / "privkey.pem"
        log.info(f"Generating immediate self-signed fallback cert for {domain}")
        Shell.run(
            "openssl req -x509 -nodes -days 825 -newkey rsa:2048 "
            f"-keyout {key_path} -out {cert_path} -subj '/CN={domain}'"
        )
        return str(cert_path), str(key_path)


class AcmeStrategy(CertStrategy):
    def issue(self, domain: str) -> tuple[str, str]:
        Shell.require("certbot", package_hint="certbot")
        log.info(f"Deploying Certbot pipeline for domain: {domain}")
        
        # Shut down Nginx momentarily to free up port 80 for the standalone challenge loop
        Shell.run("systemctl stop nginx", check=False)
        try:
            Shell.run(
                f"certbot certonly --standalone --non-interactive --agree-tos "
                f"--register-unsafely-without-email -d {domain}",
                timeout=180
            )
        except Exception as exc:
            raise CertificateError(f"ACME validation transaction dropped: {exc}")
        finally:
            Shell.run("systemctl start nginx", check=False)

        live_dir = LETSENCRYPT_LIVE / domain
        return str(live_dir / "fullchain.pem"), str(live_dir / "privkey.pem")


class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Manage automation keys and ACME cryptographic assets"
    depends_on = ["packages"]
    idempotent = False

    def is_installed(self) -> bool:
        data = state.load()
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        return bool(cert and key and Path(cert).exists() and Path(key).exists())

    def install(self) -> None:
        data = state.ensure_defaults()
        domain = data.get("cert_domain")

        if not domain:
            print()
            log.rule("Unattended ACME Configuration")
            domain = input("Target Domain name for this server: ").strip()
            if not domain:
                raise CertificateError("Domain mapping field cannot be empty.")
            state.set("cert_domain", domain)

        # Default automatically to our automated Let's Encrypt engine strategy
        strategy = AcmeStrategy()
        try:
            cert_path, key_path = strategy.issue(domain)
        except Exception:
            log.warning("ACME failed. Swapping instantly to self-signed strategy configuration.")
            strategy = SelfSignedStrategy()
            cert_path, key_path = strategy.issue(domain)

        data.update({
            "cert_domain": domain,
            "cert_fullchain_path": cert_path,
            "cert_key_path": key_path,
            "cert_issued_at": datetime.datetime.utcnow().isoformat(),
        })
        state.save(data)

        # Triggers structural rewrite inside the newly updated Nginx component block
        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()

    def remove(self) -> None:
        pass
