"""
Cloudflare Origin Certificate (interactive) or self‑signed fallback.
Uses system default DNS – no changes.
"""
from __future__ import annotations

import datetime
from pathlib import Path
import requests

from core.config import LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, state
from core.exceptions import CertificateError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class CloudflareStrategy:
    def __init__(self, email: str, api_key: str, domain: str):
        self.email = email
        self.api_key = api_key
        self.domain = domain

    def issue(self) -> tuple[str, str]:
        log.info(f"Requesting Cloudflare Origin Certificate for {self.domain}")
        headers = {
            "X-Auth-Email": self.email,
            "X-Auth-Key": self.api_key,
            "Content-Type": "application/json",
        }

        # 1. Get zone ID
        zones_url = "https://api.cloudflare.com/client/v4/zones"
        params = {"name": self.domain}
        resp = requests.get(zones_url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            raise CertificateError(f"Cloudflare API error: {resp.text}")
        data = resp.json()
        if not data.get("success") or not data.get("result"):
            raise CertificateError(f"Domain '{self.domain}' not found in your Cloudflare account.")
        zone_id = data["result"][0]["id"]

        # 2. Request origin certificate (15 years)
        cert_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/origin_certificates"
        payload = {
            "hostnames": [self.domain],
            "requested_validity": 5475,
            "request_type": "origin-rsa",
        }
        resp = requests.post(cert_url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            raise CertificateError(f"Origin cert request failed: {resp.text}")
        cert_data = resp.json()
        if not cert_data.get("success"):
            raise CertificateError(f"Origin cert error: {cert_data.get('errors')}")

        # 3. Save certificate and key
        out_dir = SSHAUTO_CERT_DIR / self.domain
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_path = out_dir / "fullchain.pem"
        key_path = out_dir / "privkey.pem"
        cert_path.write_text(cert_data["result"]["certificate"])
        key_path.write_text(cert_data["result"]["private_key"])
        log.success("Cloudflare Origin Certificate saved.")

        # Copy to Let's Encrypt path for compatibility
        le_dir = LETSENCRYPT_LIVE / self.domain
        le_dir.mkdir(parents=True, exist_ok=True)
        (le_dir / "fullchain.pem").write_text(cert_data["result"]["certificate"])
        (le_dir / "privkey.pem").write_text(cert_data["result"]["private_key"])

        return str(cert_path), str(key_path)


class SelfSignedStrategy:
    def issue(self, domain: str) -> tuple[str, str]:
        out_dir = SSHAUTO_CERT_DIR / domain
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_path = out_dir / "fullchain.pem"
        key_path = out_dir / "privkey.pem"
        log.info(f"Generating self‑signed cert for {domain}")
        Shell.run(
            "openssl req -x509 -nodes -days 825 -newkey rsa:2048 "
            f"-keyout {key_path} -out {cert_path} -subj '/CN={domain}'"
        )
        return str(cert_path), str(key_path)


class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Cloudflare Origin or self‑signed certificate"
    depends_on = ["packages"]
    idempotent = False

    def is_installed(self) -> bool:
        data = state.load()
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        return bool(cert and key and Path(cert).exists() and Path(key).exists())

    def install(self) -> None:
        """Called during `main.py install` – interactive menu."""
        print()
        log.rule("Certificate Configuration")
        self._interactive()

    def interactive(self) -> None:
        """Called during `sshauto cert` – same menu."""
        print()
        log.rule("Certificate Configuration")
        self._interactive()

    def _interactive(self) -> None:
        data = state.ensure_defaults()

        # Domain
        domain = input("Enter your domain name (e.g., hi.blackstrngr.qzz.io): ").strip()
        if not domain:
            raise CertificateError("Domain cannot be empty.")
        state.set("cert_domain", domain)
        data["cert_domain"] = domain

        # Menu
        print()
        print("Choose certificate type:")
        print("  1) Cloudflare Origin Certificate (15‑year validity)")
        print("  2) Skip – generate self‑signed certificate")
        choice = input("Enter 1 or 2: ").strip()

        if choice == "1":
            # Cloudflare
            email = input("Cloudflare Account Email: ").strip()
            if not email:
                raise CertificateError("Email is required.")
            api_key = input("Cloudflare Global API Key: ").strip()
            if not api_key:
                raise CertificateError("API Key is required.")

            strategy = CloudflareStrategy(email, api_key, domain)
            try:
                cert_path, key_path = strategy.issue()
            except Exception as e:
                log.critical(f"Cloudflare certificate failed: {e}")
                log.warning("Falling back to self‑signed.")
                strategy = SelfSignedStrategy()
                cert_path, key_path = strategy.issue(domain)
                data["cert_strategy"] = "selfsigned"
        else:
            # Self‑signed (option 2 or anything else)
            log.info("Generating self‑signed certificate.")
            strategy = SelfSignedStrategy()
            cert_path, key_path = strategy.issue(domain)
            data["cert_strategy"] = "selfsigned"

        data.update({
            "cert_fullchain_path": cert_path,
            "cert_key_path": key_path,
            "cert_issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
        })
        state.save(data)

        # Regenerate nginx
        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()

        log.success("Certificate ready and applied to nginx.")

    def remove(self) -> None:
        log.warning("Certificate removal is manual – delete files from /etc/letsencrypt/live and /var/lib/sshauto/certs if needed.")
        pass
