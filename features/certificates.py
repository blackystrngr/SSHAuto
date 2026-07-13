import os
from pathlib import Path
from core.shell import Shell
from core.logger import log
from core.config import state, LETSENCRYPT_LIVE, SSHAUTO_CERT_DIR
from features.base import BaseFeature

class CertificatesFeature(BaseFeature):
    name = "certificates"
    description = "Interactive SSL Certificate Wizard (Cloudflare API & Standalone)"
    depends_on = ["packages"]
    
    # Set to False because we don't want the auto-updater 
    # to randomly trigger interactive inputs every 30 seconds.
    idempotent = False 

    def is_installed(self) -> bool:
        return state.get("cert_strategy") is not None

    def _install_cloudflare_cert(self, domain: str):
        print("\n" + "="*50)
        print(" CLOUDFLARE API VALIDATION ".center(50))
        print("="*50)
        log.info("This method requires your Cloudflare Account Email and Global API Key.")
        log.warning("Verification is done via DNS. Port 80 does NOT need to be open.\n")

        email = input("Cloudflare Email: ").strip()
        api_key = input("Cloudflare Global API Key: ").strip()

        if not email or not api_key:
            raise Exception("Email and Global API Key are strictly required.")

        # 1. Install Certbot and the Cloudflare DNS plugin
        log.info("Installing Certbot and Cloudflare DNS plugins...")
        Shell.run("apt-get update", check=False)
        Shell.run("apt-get install -y certbot python3-certbot-dns-cloudflare", check=True)

        # 2. Create the secure credentials file
        secrets_dir = Path("/root/.secrets/certbot")
        secrets_dir.mkdir(parents=True, exist_ok=True)
        cf_ini = secrets_dir / "cloudflare.ini"

        ini_content = f"dns_cloudflare_email = {email}\ndns_cloudflare_api_key = {api_key}\n"
        cf_ini.write_text(ini_content)
        
        # Certbot will throw a security error if this file is readable by others
        os.chmod(cf_ini, 0o600)

        # 3. Request the certificate
        log.info(f"Requesting Let's Encrypt certificate for {domain} via Cloudflare API...")
        cmd = (
            f"certbot certonly --dns-cloudflare --dns-cloudflare-credentials {cf_ini} "
            f"--dns-cloudflare-propagation-seconds 20 "
            f"-d {domain} --non-interactive --agree-tos -m {email}"
        )

        res = Shell.run(cmd, check=False)
        if not res.ok:
            log.error(f"Certbot failed: {res.stderr}")
            raise Exception("Failed to acquire Cloudflare certificate. Verify your Global API Key and Domain.")

        # 4. Link and Save State
        self._symlink_certbot(domain)
        state.set("cert_strategy", "cloudflare")
        state.set("cert_domain", domain)
        log.success(f"Cloudflare SSL Certificate successfully acquired and applied for {domain}")

    def _install_http_cert(self, domain: str):
        print("\n" + "="*50)
        print(" STANDARD HTTP VALIDATION ".center(50))
        print("="*50)
        email = input("Email for expiration notices: ").strip()
        if not email: 
            raise Exception("Email required.")

        log.info("Installing Certbot...")
        Shell.run("apt-get install -y certbot", check=True)

        # Must free up port 80 for the standalone server
        Shell.run("systemctl stop nginx", check=False)

        log.info(f"Requesting HTTP-01 challenge for {domain}...")
        cmd = f"certbot certonly --standalone -d {domain} --non-interactive --agree-tos -m {email}"
        res = Shell.run(cmd, check=False)

        Shell.run("systemctl start nginx", check=False)

        if not res.ok:
            raise Exception(f"Certbot failed: {res.stderr}")

        self._symlink_certbot(domain)
        state.set("cert_strategy", "http")
        state.set("cert_domain", domain)
        log.success(f"Let's Encrypt HTTP SSL successfully acquired for {domain}")

    def _install_self_signed(self):
        log.info("Generating Self-Signed Fallback Certificate...")
        SSHAUTO_CERT_DIR.mkdir(parents=True, exist_ok=True)
        crt = SSHAUTO_CERT_DIR / "server.crt"
        key = SSHAUTO_CERT_DIR / "server.key"

        cmd = (
            f"openssl req -x509 -nodes -days 3650 -newkey rsa:2048 "
            f"-keyout {key} -out {crt} -subj '/CN=sshauto-local'"
        )
        Shell.run(cmd, check=True)

        state.set("cert_strategy", "self_signed")
        state.set("cert_domain", "localhost")
        log.success("Self-signed certificate generated. (Browsers will show a warning)")

    def _symlink_certbot(self, domain: str):
        """Links Certbot's live directory to the internal path Nginx expects."""
        SSHAUTO_CERT_DIR.mkdir(parents=True, exist_ok=True)
        live_dir = Path(f"/etc/letsencrypt/live/{domain}")

        crt_dest = SSHAUTO_CERT_DIR / "server.crt"
        key_dest = SSHAUTO_CERT_DIR / "server.key"

        Shell.run(f"rm -f {crt_dest} {key_dest}", check=False)

        os.symlink(live_dir / "fullchain.pem", crt_dest)
        os.symlink(live_dir / "privkey.pem", key_dest)

    def install(self) -> None:
        print("\n" + "="*50)
        print(" SSL/TLS CERTIFICATE WIZARD ".center(50))
        print("="*50)
        print("1. Cloudflare API (Recommended, No Port 80 needed, uses Global API Key)")
        print("2. Standard Let's Encrypt (Requires Port 80 and Domain pointed to IP)")
        print("3. Self-Signed Certificate (For internal testing)")
        print("0. Cancel")
        print("-" * 50)

        choice = input("Select option: ").strip()
        if choice == "0" or not choice:
            log.warning("Certificate generation cancelled.")
            return

        if choice in ("1", "2"):
            domain = input("\nEnter your fully qualified domain name (e.g., vpn.example.com): ").strip()
            if not domain:
                log.error("Domain is strictly required.")
                return

        if choice == "1":
            self._install_cloudflare_cert(domain)
        elif choice == "2":
            self._install_http_cert(domain)
        elif choice == "3":
            self._install_self_signed()
        else:
            log.error("Invalid choice.")
            return

        # Automatically reload Nginx to map the newly generated certificates
        res = Shell.run("systemctl is-active nginx", check=False)
        if res.ok:
            log.info("Reloading Nginx proxy to apply new certificates...")
            Shell.run("systemctl reload nginx", check=False)

    def remove(self) -> None:
        Shell.run(f"rm -rf {SSHAUTO_CERT_DIR}/*", check=False)
        state.set("cert_strategy", None)
        state.set("cert_domain", None)
        log.info("Certificates unlinked and removed from active state.")
