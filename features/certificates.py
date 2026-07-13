"""
Automated non-interactive ACME Account allocation and generation layer.
Skips re-issuance if a valid certificate already exists (matching domain).
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
        """Check if a valid certificate exists in state or on disk."""
        data = state.load()
        # Try state paths first
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        if cert and key and Path(cert).exists() and Path(key).exists():
            return True
        
        # Fallback: auto-discover from standard locations
        domain = data.get("cert_domain")
        if domain:
            # Check Let's Encrypt
            le_cert = LETSENCRYPT_LIVE / domain / "fullchain.pem"
            le_key = LETSENCRYPT_LIVE / domain / "privkey.pem"
            if le_cert.exists() and le_key.exists():
                return True
            # Check self-signed
            ss_cert = SSHAUTO_CERT_DIR / domain / "fullchain.pem"
            ss_key = SSHAUTO_CERT_DIR / domain / "privkey.pem"
            if ss_cert.exists() and ss_key.exists():
                return True
            # Check script-style selfsigned
            script_cert = Path("/etc/ssl/certs/selfsigned.crt")
            script_key = Path("/etc/ssl/private/selfsigned.key")
            if script_cert.exists() and script_key.exists():
                return True
        return False

    def install(self) -> None:
        data = state.ensure_defaults()
        
        # If a valid cert already exists, skip re-issuance
        if self.is_installed():
            log.info("Valid certificate already exists – skipping re-issuance.")
            # Ensure state is updated with discovered paths
            self._update_state_from_disk(data)
            # Regenerate nginx to apply
            from features.nginx_relay import NginxRelayFeature
            NginxRelayFeature().regenerate()
            return

        domain = data.get("cert_domain")
        if not domain:
            print()
            log.rule("Unattended ACME Configuration")
            domain = input("Target Domain name for this server: ").strip()
            if not domain:
                raise CertificateError("Domain mapping field cannot be empty.")
            state.set("cert_domain", domain)
            data["cert_domain"] = domain

        # Try ACME first
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

    def _update_state_from_disk(self, data: dict) -> None:
        """Auto-discover existing cert and update state with paths."""
        domain = data.get("cert_domain")
        if not domain:
            return

        # Let's Encrypt
        le_cert = LETSENCRYPT_LIVE / domain / "fullchain.pem"
        le_key = LETSENCRYPT_LIVE / domain / "privkey.pem"
        if le_cert.exists() and le_key.exists():
            data["cert_fullchain_path"] = str(le_cert)
            data["cert_key_path"] = str(le_key)
            state.save(data)
            return

        # Self-signed
        ss_cert = SSHAUTO_CERT_DIR / domain / "fullchain.pem"
        ss_key = SSHAUTO_CERT_DIR / domain / "privkey.pem"
        if ss_cert.exists() and ss_key.exists():
            data["cert_fullchain_path"] = str(ss_cert)
            data["cert_key_path"] = str(ss_key)
            state.save(data)
            return

        # Script selfsigned
        script_cert = Path("/etc/ssl/certs/selfsigned.crt")
        script_key = Path("/etc/ssl/private/selfsigned.key")
        if script_cert.exists() and script_key.exists():
            data["cert_fullchain_path"] = str(script_cert)
            data["cert_key_path"] = str(script_key)
            state.save(data)
            return
