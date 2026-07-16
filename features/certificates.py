import time
import datetime
import socket
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
        domain_dir = SSHAUTO_CERT_DIR / self.domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        key_path = domain_dir / "privkey.pem"
        csr_path = domain_dir / "csr.pem"

        Shell.run(f"openssl genrsa -out {key_path} 2048", check=True, timeout=10)
        subj = f"/CN={self.domain}"
        Shell.run(f"openssl req -new -key {key_path} -out {csr_path} -subj '{subj}'", check=True, timeout=10)
        return key_path.read_text(), csr_path.read_text(), str(key_path)

    def issue(self) -> tuple[str, str]:
        log.info(f"Requesting Cloudflare Origin Certificate for {self.domain}")
        key_text, csr_text, key_path = self._generate_csr()

        headers = {"Content-Type": "application/json"}
        if self.api_key.startswith("cfk_"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            if not self.email:
                raise CertificateError("Global API Key requires email.")
            headers["X-Auth-Email"] = self.email
            headers["X-Auth-Key"] = self.api_key

        payload = {
            "hostnames": [self.domain],
            "requested_validity": 5475,
            "request_type": "origin-rsa",
            "csr": csr_text.strip(),
        }

        # Resolve API IP manually (uses system DNS)
        try:
            api_ip = socket.gethostbyname("api.cloudflare.com")
            log.info(f"Resolved api.cloudflare.com -> {api_ip}")
        except Exception as e:
            log.warning(f"DNS resolution failed, using domain name fallback: {e}")
            api_ip = None

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if api_ip:
                    # Use IP with Host header
                    url = f"https://{api_ip}/client/v4/certificates"
                    headers["Host"] = "api.cloudflare.com"
                else:
                    url = "https://api.cloudflare.com/client/v4/certificates"

                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=30,
                    verify=True  # ensure SSL verification
                )
                # If success, break out of retry loop
                if resp.status_code == 200:
                    break
                else:
                    # If not 200, maybe the IP changed or certificate mismatch? We'll retry with domain.
                    if api_ip:
                        log.warning(f"Request to IP failed (status {resp.status_code}), retrying with domain...")
                        api_ip = None
                        continue
            except requests.exceptions.ConnectionError as e:
                log.warning(f"Connection error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                else:
                    raise
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                raise

        if resp.status_code != 200:
            raise CertificateError(f"Cloudflare API error: {resp.text}")

        data = resp.json()
        if not data.get("success"):
            raise CertificateError(f"Cloudflare error: {data.get('errors')}")

        cert_text = data["result"]["certificate"]
        domain_dir = SSHAUTO_CERT_DIR / self.domain
        cert_path = domain_dir / "fullchain.pem"
        cert_path.write_text(cert_text)

        le_dir = LETSENCRYPT_LIVE / self.domain
        le_dir.mkdir(parents=True, exist_ok=True)
        (le_dir / "fullchain.pem").write_text(cert_text)
        (le_dir / "privkey.pem").write_text(key_text)

        log.success("Cloudflare Origin Certificate saved.")
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

        domain = input("Enter your domain name (e.g., hi.blackstrngr.qzz.io): ").strip()
        if not domain:
            raise CertificateError("Domain cannot be empty.")
        state.set("cert_domain", domain)
        data["cert_domain"] = domain

        print()
        print("Choose certificate type:")
        print("  1) Cloudflare Origin Certificate (15‑year validity)")
        print("  2) Skip – generate self‑signed certificate")
        choice = input("Enter 1 or 2: ").strip()

        if choice == "1":
            email = input("Cloudflare Account Email: ").strip()
            if not email:
                raise CertificateError("Email is required.")
            api_key = input("Cloudflare Global API Key: ").strip()
            if not api_key:
                raise CertificateError("API Key is required.")

            strategy = CloudflareStrategy(email, api_key, domain)
            try:
                cert_path, key_path = strategy.issue()
                data["cert_strategy"] = "cloudflare"
            except Exception as e:
                log.critical(f"Cloudflare certificate failed: {e}")
                log.warning("Falling back to self‑signed.")
                strategy = SelfSignedStrategy()
                cert_path, key_path = strategy.issue(domain)
                data["cert_strategy"] = "selfsigned"
        else:
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

        from features.nginx_relay import NginxRelayFeature
        NginxRelayFeature().regenerate()

        log.success("Certificate ready and applied to nginx.")

    def remove(self) -> None:
        log.warning("Certificate removal is manual – delete files from /etc/letsencrypt/live and /var/lib/sshauto/certs if needed.")
        pass


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
