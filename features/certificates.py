import os
import json
import urllib.request
import urllib.error
from pathlib import Path
from core.shell import Shell
from core.logger import log
from core.config import state
from features.base import BaseFeature

class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Direct Cloudflare Origin CA Certificate Provisioner"
    depends_on = ["packages"]
    
    # Prevents background auto-installer runs from prompting console inputs
    idempotent = False 

    def is_installed(self) -> bool:
        return state.get("cert_strategy") == "cloudflare_origin" and \
               os.path.exists("/var/lib/sshauto/certs/server.crt")

    def _generate_cloudflare_origin_cert(self, domain: str):
        print("\n" + "="*60)
        print(" CLOUDFLARE ORIGIN CA DIRECT PROVISIONER ".center(60))
        print("="*60)
        log.info("This will fetch a 15-year trusted Origin Certificate directly from Cloudflare.")
        log.warning("Ensure your Cloudflare proxy (Orange Cloud) is ENABLED for this domain.\n")

        email = input("Cloudflare Account Email: ").strip()
        api_key = input("Cloudflare Global API Key: ").strip()

        if not email or not api_key:
            raise Exception("Both Email and Global API Key are strictly required.")

        # 1. Prepare secure filesystem directory structures
        cert_dir = Path("/var/lib/sshauto/certs")
        cert_dir.mkdir(parents=True, exist_ok=True)
        
        key_path = cert_dir / "server.key"
        crt_path = cert_dir / "server.crt"
        csr_path = Path("/tmp/sshauto_cf.csr")

        # 2. Local cryptographic key and signing request initialization
        log.info("Generating local Private Key and Certificate Signing Request (CSR)...")
        Shell.run(f"rm -f {key_path} {crt_path} {csr_path}", check=False)
        
        Shell.run(f"openssl genrsa -out {key_path} 2048", check=True)
        Shell.run(f"openssl req -new -key {key_path} -out {csr_path} -subj '/CN={domain}'", check=True)
        
        csr_content = csr_path.read_text()

        # 3. Payload configuration targeting Cloudflare v4 Certificate endpoints
        url = "https://api.cloudflare.com/client/v4/certificates"
        headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "hostnames": [domain, f"*.{domain}"],
            "requested_validity": 5475,  # 15 Years validation period
            "request_type": "origin-rsa",
            "csr": csr_content
        }

        log.info("Transmitting CSR directly to Cloudflare API...")
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                
                if res_data.get("success"):
                    cert_content = res_data["result"]["certificate"]
                    crt_path.write_text(cert_content.strip() + "\n")
                    
                    # Lock down absolute file permissions for local keys
                    os.chmod(key_path, 0o600)
                    os.chmod(crt_path, 0o644)
                    
                    state.set("cert_strategy", "cloudflare_origin")
                    state.set("cert_domain", domain)
                    log.success(f"15-Year Cloudflare Certificate successfully generated for {domain}")
                else:
                    errors = res_data.get("errors", [])
                    raise Exception(f"Cloudflare API rejected request: {errors}")
                    
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                parsed_err = json.loads(err_body)
                err_msg = parsed_err.get("errors", [{}])[0].get("message", err_body)
            except Exception:
                err_msg = err_body
            raise Exception(f"Cloudflare API Connection Failed ({e.code}): {err_msg}")
        except Exception as e:
            raise Exception(f"Failed to communicate with Cloudflare: {e}")
        finally:
            if csr_path.exists():
                csr_path.unlink()

    def install(self) -> None:
        print("\n" + "="*50)
        print(" CERTIFICATE MANAGEMENT ".center(50))
        print("="*50)
        print("1. Provision Direct Cloudflare Origin CA Certificate (15 Years)")
        print("0. Cancel / Skip")
        print("-" * 50)

        choice = input("Select option: ").strip()
        if choice != "1":
            log.warning("Certificate generation skipped.")
            return

        domain = input("\nEnter your domain/subdomain (e.g., node1.yourdomain.com): ").strip()
        if not domain:
            log.error("Domain cannot be empty.")
            return

        self._generate_cloudflare_origin_cert(domain)

        # Notify reverse-proxy servers to pick up the updated keymaps
        if Shell.run("systemctl is-active nginx", check=False).ok:
            log.info("Reloading Nginx to apply new Origin Certificate...")
            Shell.run("systemctl reload nginx", check=False)

    def remove(self) -> None:
        Shell.run("rm -f /var/lib/sshauto/certs/server.crt /var/lib/sshauto/certs/server.key", check=False)
        state.set("cert_strategy", None)
        state.set("cert_domain", None)
        log.info("Cloudflare certificates securely deleted from state.")
