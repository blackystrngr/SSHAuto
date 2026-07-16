"""
Cloudflare Origin Certificate (interactive) or automatic self‑signed.
During full install: auto‑generates self‑signed if no cert exists.
During `sshauto cert`: prompts for Cloudflare details.
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

        zones_url = "https://api.cloudflare.com/client/v4/zones"
        params = {"name": self.domain}
        resp = requests.get(zones_url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            raise CertificateError(f"Cloudflare API error: {resp.text}")
        data = resp.json()
        if not data.get("success") or not data.get("result"):
            raise CertificateError(f"Domain '{self.domain}' not found.")
        zone_id = data["result"][0]["id"]

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

        out_dir = SSHAUTO_CERT_DIR / self.domain
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_path = out_dir / "fullchain.pem"
        key_path = out_dir / "privkey.pem"
        cert_path.write_text(cert_data["result"]["certificate"])
        key_path.write_text(cert_data["result"]["private_key"])
        log.success("Cloudflare Origin Certificate saved.")

        # Also copy to Let's Encrypt style path
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
    description = "Manage Cloudflare Origin or self‑signed certificates"
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

        # ---- If called from full install (non‑interactive) ----
        # If domain is missing or cert files don't exist, generate self‑signed.
        if not domain or not self._cert_files_exist(data):
            log.info("No valid certificate found. Generating self‑signed automatically.")
            if not domain:
                domain = input("Enter your domain name: ").strip()
                if not domain:
                    raise CertificateError("Domain cannot be empty.")
                state.set("cert_domain", domain)
                data["cert_domain"] = domain
            strategy = SelfSignedStrategy()
            cert_path, key_path = strategy.issue(domain)
            data.update({
                "cert_fullchain_path": cert_path,
                "cert_key_path": key_path,
                "cert_issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "cert_strategy": "selfsigned",
            })
            state.save(data)
            from features.nginx_relay import NginxRelayFeature
            NginxRelayFeature().regenerate()
            log.success("Self‑signed certificate ready.")
            return

        # ---- If called via `sshauto cert` (interactive) ----
        self._interactive_install(data)

    def _interactive_install(self, data: dict) -> None:
        """Interactive Cloudflare certificate wizard."""
        print()
        log.rule("Certificate Configuration")
        domain = data.get("cert_domain")
        if domain:
            print(f"Current domain: {domain}")
            change = input("Change domain? [y/N]: ").strip().lower()
            if change in ("y", "yes"):
                domain = input("New domain: ").strip()
                if not domain:
                    raise CertificateError("Domain cannot be empty.")
                state.set("cert_domain", domain)
                data["cert_domain"] = domain
        else:
            domain = input("Enter your domain name: ").strip()
            if not domain:
                raise CertificateError("Domain cannot be empty.")
            state.set("cert_domain", domain)
            data["cert_domain"] = domain

        print()
        log.important("Cloudflare Origin Certificate (15‑year validity) is recommended.")
        use_cf = input("Do you want to use Cloudflare? [Y/n]: ").strip().lower()

        if use_cf in ("y", "yes", ""):
            email = input("Cloudflare Account Email: ").strip()
            if not email:
                log.warning("Email is required. Falling back to self‑signed.")
                use_cf = "n"
            else:
                api_key = input("Cloudflare Global API Key: ").strip()
                if not api_key:
                    log.warning("API Key is required. Falling back to self‑signed.")
                    use_cf = "n"
                else:
                    strategy = CloudflareStrategy(email, api_key, domain)
                    try:
                        cert_path, key_path = strategy.issue()
                        data.update({
                            "cert_fullchain_path": cert_path,
                            "cert_key_path": key_path,
                            "cert_issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
                            "cert_strategy": "cloudflare",
                        })
                        state.save(data)
                        from features.nginx_relay import NginxRelayFeature
                        NginxRelayFeature().regenerate()
                        log.success("Certificate ready and applied to nginx.")
                        return
                    except Exception as e:
                        log.error(f"Cloudflare failed: {e}")
                        log.warning("Falling back to self‑signed.")
                        use_cf = "n"

        if use_cf not in ("y", "yes", ""):
            log.info("Using self‑signed certificate.")
            strategy = SelfSignedStrategy()
            cert_path, key_path = strategy.issue(domain)
            data.update({
                "cert_fullchain_path": cert_path,
                "cert_key_path": key_path,
                "cert_issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "cert_strategy": "selfsigned",
            })
            state.save(data)
            from features.nginx_relay import NginxRelayFeature
            NginxRelayFeature().regenerate()
            log.success("Self‑signed certificate ready.")

    def _cert_files_exist(self, data: dict) -> bool:
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        return bool(cert and key and Path(cert).exists() and Path(key).exists())

    def remove(self) -> None:
        log.warning("Certificate removal is manual – delete files from /etc/letsencrypt/live and /var/lib/sshauto/certs if needed.")
