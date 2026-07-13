import os
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return os.path.exists("/opt/sshauto/ws_proxy.py") and \
               Shell.run("systemctl is-active sshauto-proxy", check=False).ok

    def install(self) -> None:
        log.info("Installing/Updating Python Proxy service...")
        
        # The proxy logic (the actual tunnel handler)
        script_content = r'''
import asyncio
import sys

# Configuration
BACKEND_IP = "127.0.0.1"
BACKEND_PORT = 113  # FIX 1: Changed from 110 to 113 (Dropbear default)
LISTEN_PORT = 8000

async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(4096)
            if not data: break
            writer.write(data)
            await writer.drain()
    except Exception: pass
    finally:
        writer.close()

async def handle(client_reader, client_writer):
    try:
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = await client_reader.read(1024)
            if not chunk: return
            header += chunk
        
        # FIX 2: Rescue the SSH handshake bytes swallowed during the header read
        idx = header.find(b"\r\n\r\n")
        trailing = header[idx+4:]
        
        client_writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await client_writer.drain()
        
        try:
            d_reader, d_writer = await asyncio.open_connection(BACKEND_IP, BACKEND_PORT)
        except Exception: return

        # FIX 2 (Continued): Immediately push rescued SSH bytes to Dropbear
        if trailing:
            d_writer.write(trailing)
            await d_writer.drain()

        await asyncio.gather(pipe(client_reader, d_writer), pipe(d_reader, client_writer))
    except Exception: pass
    finally:
        client_writer.close()

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    async with server: await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
'''
        with open("/opt/sshauto/ws_proxy.py", "w") as f:
            f.write(script_content.strip() + "\n")

        # Create systemd service
        service_path = "/etc/systemd/system/sshauto-proxy.service"
        with open(service_path, "w") as f:
            f.write("""[Unit]
Description=SSHAuto WebSocket Proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/sshauto/ws_proxy.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
""")

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable --now sshauto-proxy")
        Shell.run("systemctl restart sshauto-proxy")
        
        # STRICT ENFORCEMENT
        if not self.is_installed():
            raise Exception("Critical Failure: Python Proxy failed to start.")
        log.success("Python Proxy installed and verified.")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("rm -f /etc/systemd/system/sshauto-proxy.service", check=False)
        Shell.run("rm -f /opt/sshauto/ws_proxy.py", check=False)
        Shell.run("systemctl daemon-reload", check=False)
        log.info("Python Proxy removed")
