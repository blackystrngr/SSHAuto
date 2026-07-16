"""
Cloudflare Origin Certificate (interactive). Uses the /certificates endpoint.
Supports both Global API Key (with email) and API Token (Bearer).
"""
from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
import requests

from core.config import LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, state
from core.exceptions import CertificateError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class CloudflareStrategy:
    def __init__(self, email: str | None, api_key: str, domain: str):
        self.email = email
        self.api_key = api_key
        self.domain = domain

    def _generate_csr(self) -> tuple[str, str, str]:
        """Generate private key and CSR using OpenSSL. Returns (key, csr, key_path)."""
        key_path = SSHAUTO_CERT_DIR / self.domain / "privkey.pem"
        csr_path = SSHAUTO_CERT_DIR / self.domain / "csr.pem"
        SSHAUTO_CERT_DIR.mkdir(parents=True, exist_ok=True)

        # Generate private key
        Shell.run(
            f"openssl genrsa -out {key_path} 2048",
            check=True,
            timeout=10
        )

        # Generate CSR
        subj = f"/CN={self.domain}"
        Shell.run(
            f"openssl req -new -key {key_path} -out {csr_path} -subj '{subj}'",
            check=True,
            timeout=10
        )

        csr_text = csr_path.read_text()
        key_text = key_path.read_text()
        return key_text, csr_text, str(key_path)

    def issue(self) -> tuple[str, str]:
        log.info(f"Requesting Cloudflare Origin Certificate for {self.domain}")

        # Generate CSR
        key_text, csr_text, key_path = self._generate_csr()

        # Build headers
        headers = {"Content-Type": "application/json"}
        # If API key starts with "cfk_", treat as token (Bearer)
        if self.api_key.startswith("cfk_"):
            headers["Authorization"] = f"Bearer {self.api_key}"
            log.info("Using API Token (Bearer) authentication.")
        else:
            # Global API Key – requires email
            if not self.email:
                raise CertificateError("Global API Key requires email address.")
            headers["X-Auth-Email"] = self.email
            headers["X-Auth-Key"] = self.api_key
            log.info("Using Global API Key authentication.")

        payload = {
            "hostnames": [self.domain],
            "requested_validity": 5475,
            "request_type": "origin-rsa",
            "csr": csr_text.strip(),
        }

        resp = requests.post(
            "https://api.cloudflare.com/client/v4/certificates",
            headers=headers,
            json=payload,
            timeout=30
        )

        if resp.status_code != 200:
            raise CertificateError(f"Cloudflare API error (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        if not data.get("success"):
            errors = data.get("errors", [])
            raise CertificateError(f"Cloudflare error: {errors}")

        result = data["result"]
        cert_text = result["certificate"]

        # Save fullchain (certificate only; Cloudflare returns the full chain)
        cert_path = SSHAUTO_CERT_DIR / self.domain / "fullchain.pem"
        cert_path.write_text(cert_text)

        # Also store in Let's Encrypt style path for compatibility
        le_dir = LETSENCRYPT_LIVE / self.domain
        le_dir.mkdir(parents=True, exist_ok=True)
        (le_dir / "fullchain.pem").write_text(cert_text)
        (le_dir / "privkey.pem").write_text(key_text)

        log.success("Cloudflare Origin Certificate saved.")
        return str(cert_path), str(key_path)


class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Cloudflare Origin Certificate (interactive)"
    depends_on = ["packages"]
    idempotent = False

    def is_installed(self) -> bool:
        data = state.load()
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        return bool(cert and key and Path(cert).exists() and Path(key).exists())

    def install(self) -> None:
        """Called during `main.py install` – interactive Cloudflare wizard."""
        print()
        log.rule("Cloudflare Certificate Configuration")
        self._interactive()

    def interactive(self) -> None:
        """Called during `sshauto cert` – same wizard."""
        print()
        log.rule("Cloudflare Certificate Configuration")
        self._interactive()

    def _interactive(self) -> None:
        data = state.ensure_defaults()

        domain = input("Enter your domain name (e.g., hi.blackstrngr.qzz.io): ").strip()
        if not domain:
            raise CertificateError("Domain cannot be empty.")
        state.set("cert_domain", domain)
        data["cert_domain"] = domain

        # Ask for API key type
        print()
        api_type = input("Use Global API Key (with email) or API Token? [G/T] (default G): ").strip().upper()
        if api_type == "T":
            api_key = input("Cloudflare API Token (starts with cfk_): ").strip()
            if not api_key:
                raise CertificateError("API Token is required.")
            email = None
        else:
            email = input("Cloudflare Account Email: ").strip()
            if not email:
                raise CertificateError("Email is required.")
            api_key = input("Cloudflare Global API Key: ").strip()
            if not api_key:
                raise CertificateError("Global API Key is required.")

        strategy = CloudflareStrategy(email, api_key, domain)
        try:
            cert_path, key_path = strategy.issue()
        except Exception as e:
            log.critical(f"Cloudflare certificate failed: {e}")
            raise CertificateError(f"Cloudflare certificate issuance failed: {e}")

        data.update({
            "cert_fullchain_path": cert_path,
            "cert_key_path": key_path,
            "cert_issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "cert_strategy": "cloudflare",
        })
        state.save(data)

        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()

        log.success("Cloudflare certificate ready and applied to nginx.")

    def remove(self) -> None:
        log.warning("Certificate removal is manual – delete files from /etc/letsencrypt/live and /var/lib/sshauto/certs if needed.")
        pass
