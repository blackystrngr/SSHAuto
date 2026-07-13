import os
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    depends_on = ["packages"]  # Ensure dependencies are met
    
    def is_installed(self) -> bool:
        # Check if the service is running and the script exists
        return os.path.exists("/opt/sshauto/ws_proxy.py") and \
               Shell.run("systemctl is-active sshauto-proxy", check=False).ok

    def install(self) -> None:
        log.info("Installing/Updating Python Proxy service...")
        
        # 1. Write the robust proxy script to the system
        script_content = r'''
import asyncio
import sys

# Dropbear usually runs on port 110 or 443 depending on your config
BACKEND_IP = "127.0.0.1"
BACKEND_PORT = 110 
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
        # Permissive handshake: Read until headers finish
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = await client_reader.read(1024)
            if not chunk: return
            header += chunk
        
        # Force a response that satisfies any standard websocket client
        client_writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await client_writer.drain()
        
        # Connect to Dropbear
        try:
            d_reader, d_writer = await asyncio.open_connection(BACKEND_IP, BACKEND_PORT)
        except Exception: return

        # Bridge connections
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
            f.write(script_content)

        # 2. Create systemd service if it doesn't exist
        service_path = "/etc/systemd/system/sshauto-proxy.service"
        service_content = """[Unit]
Description=SSHAuto WebSocket Proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/sshauto/ws_proxy.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
"""
        with open(service_path, "w") as f:
            f.write(service_content)

        Shell.run("systemctl daemon-reload")
        Shell.run("systemctl enable --now sshauto-proxy")
        log.success("Python Proxy installed and active")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("rm /etc/systemd/system/sshauto-proxy.service", check=False)
        log.info("Python Proxy removed")
