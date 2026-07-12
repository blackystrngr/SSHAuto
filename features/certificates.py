"""
Certificate resolution, in priority order:

  1. If a valid, non-expired cert is already on disk for the chosen
     domain (Let's Encrypt live dir OR our own sshauto cert dir), reuse
     it silently — the interactive menu is skipped entirely, per spec.
  2. Otherwise, prompt the operator to choose:
       [1] Self-signed   - instant, works with Cloudflare "Full" mode
       [2] ACME           - certbot, auto-registers an account, real
                            browser-trusted cert (needs a real domain
                            pointed at this server, HTTP-01 challenge)
       [3] Cloudflare      - email + Global API Key -> Cloudflare Origin
                            CA cert (works with Cloudflare "Full (strict)",
                            15-year validity, no rate limits)

Whichever strategy runs, it writes cert_fullchain_path/cert_key_path into
state.json so nginx_relay.py can pick them up.
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from core.config import LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR, state
from core.exceptions import CertificateError
from core.logger import log
from core.shell import Shell
from features.base import BaseFeature


class CertStrategy:
    """Base class for a single certificate-issuance method."""

    label = "base"

    def issue(self, domain: str, **kwargs) -> tuple[str, str]:
        """Return (fullchain_path, key_path)."""
        raise NotImplementedError


class SelfSignedStrategy(CertStrategy):
    label = "Self-signed certificate"

    def issue(self, domain: str, **kwargs) -> tuple[str, str]:
        out_dir = SSHAUTO_CERT_DIR / domain
        out_dir.mkdir(parents=True, exist_ok=True)
        cert_path = out_dir / "fullchain.pem"
        key_path = out_dir / "privkey.pem"

        log.info(f"generating a 825-day self-signed cert for {domain}")
        Shell.run(
            "openssl req -x509 -nodes -days 825 -newkey rsa:2048 "
            f"-keyout {key_path} -out {cert_path} "
            f'-subj "/CN={domain}"',
        )
        return str(cert_path), str(key_path)


class AcmeStrategy(CertStrategy):
    label = "ACME (Let's Encrypt via certbot, auto account creation)"

    def issue(self, domain: str, email: str | None = None, **kwargs) -> tuple[str, str]:
        Shell.require("certbot", package_hint="certbot")
        email_arg = f"--email {email} --no-eff-email" if email else "--register-unsafely-without-email"

        log.info(f"requesting ACME cert for {domain} (standalone HTTP-01 on :80)")
        # standalone needs port 80 briefly free; stop nginx for the challenge
        Shell.run("systemctl stop nginx", check=False)
        try:
            Shell.run(
                f"certbot certonly --standalone --non-interactive --agree-tos "
                f"{email_arg} -d {domain}",
                timeout=180,
            )
        except Exception as exc:
            raise CertificateError(
                f"ACME issuance failed for {domain}: {exc}",
                hint="Make sure the domain's A/AAAA record points at this "
                     "server and port 80 is reachable from the internet.",
            ) from exc
        finally:
            Shell.run("systemctl start nginx", check=False)

        live_dir = LETSENCRYPT_LIVE / domain
        return str(live_dir / "fullchain.pem"), str(live_dir / "privkey.pem")


class CloudflareStrategy(CertStrategy):
    label = "Cloudflare Origin CA (email + Global API Key)"

    API_URL = "https://api.cloudflare.com/client/v4/certificates"

    def issue(self, domain: str, email: str | None = None,
              api_key: str | None = None, **kwargs) -> tuple[str, str]:
        if not email or not api_key:
            raise CertificateError("Cloudflare strategy needs both email and Global API Key")
        try:
            import requests
        except ImportError as exc:
            raise CertificateError(
                "python 'requests' package missing",
                hint="pip3 install --break-system-packages requests",
            ) from exc

        out_dir = SSHAUTO_CERT_DIR / domain
        out_dir.mkdir(parents=True, exist_ok=True)
        key_path = out_dir / "privkey.pem"
        cert_path = out_dir / "fullchain.pem"

        log.info(f"generating CSR for {domain}, requesting Cloudflare Origin CA cert")
        Shell.run(f"openssl genrsa -out {key_path} 2048")
        csr = Shell.run(
            f'openssl req -new -key {key_path} -subj "/CN={domain}"'
        ).stdout

        payload = {
            "hostnames": [domain, f"*.{domain}"],
            "requested_validity": 5475,   # 15 years
            "request_type": "origin-rsa",
            "csr": csr,
        }
        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=30)
        body = resp.json()
        if not resp.ok or not body.get("success"):
            errors = body.get("errors", resp.text)
            raise CertificateError(f"Cloudflare API rejected the request: {errors}")

        cert_pem = body["result"]["certificate"]
        cert_path.write_text(cert_pem)
        log.success("Cloudflare Origin CA certificate issued (valid 15 years)")
        log.important(
            "Remember: set the SSL/TLS mode for this zone to 'Full (strict)' "
            "in the Cloudflare dashboard so the edge trusts this origin cert."
        )
        return str(cert_path), str(key_path)


STRATEGIES: dict[str, type[CertStrategy]] = {
    "1": SelfSignedStrategy,
    "2": AcmeStrategy,
    "3": CloudflareStrategy,
}


class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Resolve/issue the TLS certificate used by the nginx relay"
    depends_on = ["packages"]
    # NOT auto re-run on every update — re-issuing certs is an explicit,
    # operator-triggered action (`sshauto cert`), not part of every deploy.
    idempotent = False

    def is_installed(self) -> bool:
        data = state.load()
        cert = data.get("cert_fullchain_path")
        key = data.get("cert_key_path")
        return bool(cert and key and Path(cert).exists() and Path(key).exists()
                     and self._is_valid(cert))

    def install(self) -> None:
        data = state.ensure_defaults()
        domain = data.get("cert_domain")

        existing = self._find_existing_valid_cert(domain) if domain else None
        if existing:
            cert_path, key_path = existing
            log.success(f"valid certificate already present for {domain}, reusing it "
                         "(skipping the certificate menu)")
            self._save(domain, cert_path, key_path, data.get("cert_strategy", "existing"))
            return

        domain, strategy_key, extra = self._prompt_choice(domain)
        strategy = STRATEGIES[strategy_key]()
        cert_path, key_path = strategy.issue(domain, **extra)
        self._save(domain, cert_path, key_path, strategy.label)

    def remove(self) -> None:
        log.warning("certificates.remove() does not delete issued certs from disk; "
                     "clear state.json's cert_* keys manually if you really want to")

    # -- interactive prompt (runs on the operator's terminal) -----------
    def _prompt_choice(self, existing_domain: str | None):
        print()
        log.rule("TLS certificate setup")
        domain = input(f"Domain for this server [{existing_domain or ''}]: ").strip() or existing_domain
        if not domain:
            raise CertificateError("a domain is required to issue/select a certificate")

        print(
            "\n  1) Self-signed certificate            (instant, no domain validation)\n"
            "  2) ACME / Let's Encrypt (certbot)      (auto-creates account, real trusted cert)\n"
            "  3) Cloudflare Origin CA                (email + Global API Key)\n"
        )
        choice = ""
        while choice not in STRATEGIES:
            choice = input("Choose [1-3]: ").strip()

        extra = {}
        if choice == "2":
            extra["email"] = input("Email for ACME account (blank = registerless): ").strip() or None
        elif choice == "3":
            extra["email"] = input("Cloudflare account email: ").strip()
            extra["api_key"] = input("Cloudflare Global API Key: ").strip()

        state.set("cert_domain", domain)
        return domain, choice, extra

    # -- existing-cert detection -----------------------------------------
    def _find_existing_valid_cert(self, domain: str) -> tuple[str, str] | None:
        candidates = [
            (LETSENCRYPT_LIVE / domain / "fullchain.pem", LETSENCRYPT_LIVE / domain / "privkey.pem"),
            (SSHAUTO_CERT_DIR / domain / "fullchain.pem", SSHAUTO_CERT_DIR / domain / "privkey.pem"),
        ]
        for cert, key in candidates:
            if cert.exists() and key.exists() and self._is_valid(str(cert)):
                return str(cert), str(key)
        return None

    def _is_valid(self, cert_path: str) -> bool:
        """True if the cert exists, parses, and isn't expired (checkend 0)."""
        result = Shell.run(f"openssl x509 -in {cert_path} -checkend 0 -noout", check=False)
        return result.ok

    def _save(self, domain: str, cert_path: str, key_path: str, strategy_label: str):
        data = state.ensure_defaults()
        data.update({
            "cert_domain": domain,
            "cert_fullchain_path": cert_path,
            "cert_key_path": key_path,
            "cert_strategy": strategy_label,
            "cert_issued_at": datetime.datetime.utcnow().isoformat(),
        })
        state.save(data)

        # re-render the nginx relay now that a cert exists
        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()
