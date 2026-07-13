import os
from core.shell import Shell
from core.logger import log

class PythonProxyFeature:
    def __init__(self):
        self.proxy_script = "/opt/sshauto/ws_proxy.py"
        self.service_file = "/etc/systemd/system/sshauto-proxy.service"
        self.sshd_config = "/etc/ssh/sshd_config"

    def _harden_ssh_server(self):
        """Safely injects MaxSessions and MaxStartups into sshd_config."""
        log.info("Hardening SSH configuration for stable tunnels...")
        if os.path.exists(self.sshd_config):
            with open(self.sshd_config, "r") as f:
                lines = f.readlines()
            
            # Remove any existing directives to avoid duplicates
            with open(self.sshd_config, "w") as f:
                for line in lines:
                    if "MaxSessions" not in line and "MaxStartups" not in line:
                        f.write(line)
                # Append the new optimized limits
                f.write("\n# Optimized for WebSocket Tunneling / MUX\n")
                f.write("MaxSessions 100\n")
                f.write("MaxStartups 100:30:200\n")
            
            # Apply changes
            Shell.run("systemctl restart ssh", check=False)
            log.info("SSH limits increased and service restarted.")
        else:
            log.warning(f"Could not find {self.sshd_config}. Skipping SSH hardening.")

    def install(self):
        # 1. Apply SSH Tuning first
        self._harden_ssh_server()

        # 2. Write the Atomic Proxy Script (Fixes stuck handshakes)
        log.info("Deploying Atomic WebSocket script...")
        script_content = r'''
import asyncio

# Configuration
BACKEND_IP = "127.0.0.1"
BACKEND_PORT = 110  # Dropbear port
LISTEN_PORT = 8000

async def pipe(reader, writer):
    """Bridge two streams until one closes."""
    try:
        while True:
            data = await reader.read(8192)
            if not data: break
            writer.write(data)
            await writer.drain()
    except Exception: pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except: pass

async def handle(client_reader, client_writer):
    """Atomic proxy: Handshake immediately, then tunnel."""
    try:
        # 1. Read the initial burst (handshake request)
        await client_reader.read(1024)

        # 2. Immediately send the "Switching Protocols" response
        client_writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                            b"Upgrade: websocket\r\n"
                            b"Connection: Upgrade\r\n\r\n")
        await client_writer.drain()

        # 3. Connect to Dropbear Backend
        try:
            d_reader, d_writer = await asyncio.open_connection(BACKEND_IP, BACKEND_PORT)
        except Exception:
            return

        # 4. Bridge connections Bidirectionally
        await asyncio.gather(pipe(client_reader, d_writer), pipe(d_reader, client_writer))

    except Exception: pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except: pass

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    async with server: await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
'''
        os.makedirs("/opt/sshauto", exist_ok=True)
        with open(self.proxy_script, "w") as f:
            f.write(script_content.strip())

        # 3. Create and start systemd service
        service_content = f"""[Unit]
Description=SSHAuto WebSocket Proxy
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 {self.proxy_script}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        with open(self.service_file, "w") as f:
            f.write(service_content)

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable sshauto-proxy")
        Shell.run("systemctl restart sshauto-proxy")
        log.info("WebSocket proxy installed and running on port 8000.")

    def is_installed(self):
        return os.path.exists(self.proxy_script)

    def remove(self):
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("systemctl disable sshauto-proxy", check=False)
        if os.path.exists(self.service_file):
            os.remove(self.service_file)
        if os.path.exists(self.proxy_script):
            os.remove(self.proxy_script)
        Shell.run("systemctl daemon-reload")
        log.info("WebSocket proxy removed.")
