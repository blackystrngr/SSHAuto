import os
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    description = "Atomic cryptographic WebSocket-to-TCP proxy with SSH server tuning"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return os.path.exists("/opt/sshauto/ws_proxy.py") and \
               Shell.run("systemctl is-active sshauto-proxy", check=False).ok

    def _harden_ssh_server(self) -> None:
        """Safely cleans and updates sshd_config limits to prevent concurrent blocks."""
        sshd_config = "/etc/ssh/sshd_config"
        if os.path.exists(sshd_config):
            log.info("Optimizing SSH server settings for maximum concurrent connections...")
            with open(sshd_config, "r") as f:
                lines = f.readlines()
            
            # Filter out old or duplicated limits
            with open(sshd_config, "w") as f:
                for line in lines:
                    if "MaxSessions" not in line and "MaxStartups" not in line:
                        f.write(line)
                
                # Append optimized multi-session tuning rules
                f.write("\n# Optimized for WebSocket Tunneling / Multiplexing\n")
                f.write("MaxSessions 100\n")
                f.write("MaxStartups 100:30:200\n")
            
            Shell.run("systemctl restart ssh", check=False)
            log.info("SSH service limits updated and service restarted successfully.")
        else:
            log.warning(f"Could not locate {sshd_config}. Skipping SSH tuning layer.")

    def install(self) -> None:
        log.info("Installing/Updating Python Proxy service...")
        
        # 1. Apply Server-Side High Concurrency Tuning
        self._harden_ssh_server()
        
        # 2. Write Out the Atomic Cryptographic Proxy script
        script_content = r'''
import asyncio
import hashlib
import base64
import json
from pathlib import Path

# Configuration & State Mapping
STATE_PATH = Path("/var/lib/sshauto/state.json")
BACKEND_IP = "127.0.0.1"
DROPBEAR_PORT_DEFAULT = 113  # Sourced directly from config.py specification
LISTEN_PORT = 8000

def get_live_dropbear_port():
    """Dynamically syncs with the current active Dropbear port state."""
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text())
            return data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)
    except Exception:
        pass
    return DROPBEAR_PORT_DEFAULT

async def pipe(reader, writer):
    """Bridge data bidirectionally between client and backend streams."""
    try:
        while True:
            data = await reader.read(16384)
            if not data: 
                break
            writer.write(data)
            await writer.drain()
    except Exception: 
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except: 
            pass

async def handle(client_reader, client_writer):
    """Parses incoming WebSocket frames, responds signature header, and proxies."""
    try:
        header_data = b""
        # Gather HTTP headers securely without locking up connection loop
        while b"\r\n\r\n" not in header_data and len(header_data) < 4096:
            chunk = await client_reader.read(1024)
            if not chunk: 
                break
            header_data += chunk

        if not header_data:
            return

        # Locate unique handshake keys for standard CDN/Upstream requests
        ws_key = None
        lines = header_data.decode("utf-8", errors="ignore").split("\r\n")
        for line in lines:
            if line.lower().startswith("sec-websocket-key:"):
                ws_key = line.split(":", 1)[1].strip()
                break

        # Calculate a cryptographically compliant WebSocket handshake acceptance payload
        if ws_key:
            magic_guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            sha1_hash = hashlib.sha1((ws_key + magic_guid).encode()).digest()
            accept_token = base64.b64encode(sha1_hash).decode()
            
            response = (
                f"HTTP/1.1 101 Switching Protocols\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_token}\r\n\r\n"
            ).encode()
        else:
            # Flexible structural fallback for direct legacy raw payload setups
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n\r\n"
            ).encode()

        client_writer.write(response)
        await client_writer.drain()

        # Resolve live Dropbear server target destination
        backend_port = get_live_dropbear_port()
        try:
            d_reader, d_writer = await asyncio.open_connection(BACKEND_IP, backend_port)
        except Exception:
            return

        # Merge active full-duplex data tunnels
        await asyncio.gather(pipe(client_reader, d_writer), pipe(d_reader, client_writer))
    except Exception: 
        pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except: 
            pass

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    async with server: 
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
'''
        os.makedirs("/opt/sshauto", exist_ok=True)
        with open("/opt/sshauto/ws_proxy.py", "w") as f:
            f.write(script_content.strip() + "\n")

        # 3. Create Systemd service
        service_path = "/etc/systemd/system/sshauto-proxy.service"
        with open(service_path, "w") as f:
            f.write("""[Unit]
Description=SSHAuto WebSocket Proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/sshauto/ws_proxy.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
""")

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable --now sshauto-proxy")
        
        # 4. STRICT ENFORCEMENT
        if not self.is_installed():
            raise Exception("Critical Failure: Python Proxy failed to start.")
        log.success("Python Proxy installed and verified.")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("systemctl disable sshauto-proxy", check=False)
        Shell.run("rm -f /etc/systemd/system/sshauto-proxy.service", check=False)
        Shell.run("rm -f /opt/sshauto/ws_proxy.py", check=False)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Python Proxy removed")
