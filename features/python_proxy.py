# ... inside the install() method of your PythonProxyFeature class ...
        script_content = r'''
import asyncio
import sys

# Configuration
BACKEND_IP = "127.0.0.1"
BACKEND_PORT = 110 # Ensure this is your Dropbear port
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
        # 1. Read the initial burst (the handshake request)
        # We don't parse it. We just need to clear the buffer.
        await client_reader.read(1024)

        # 2. Immediately send the "Switching Protocols" response
        # This tricks the client into thinking the handshake was perfect
        client_writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                            b"Upgrade: websocket\r\n"
                            b"Connection: Upgrade\r\n\r\n")
        await client_writer.drain()

        # 3. Connect to Dropbear
        try:
            d_reader, d_writer = await asyncio.open_connection(BACKEND_IP, BACKEND_PORT)
        except Exception:
            return

        # 4. Bridge connections (Bidirectional)
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

        with open("/opt/sshauto/ws_proxy.py", "w") as f:
            f.write(script_content)

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
        
        # STRICT ENFORCEMENT
        if not self.is_installed():
            raise Exception("Critical Failure: Python Proxy failed to start.")
        log.success("Python Proxy installed and verified.")

    def remove(self) -> None:
        Shell.run("systemctl stop sshauto-proxy", check=False)
        Shell.run("rm /etc/systemd/system/sshauto-proxy.service", check=False)
        log.info("Python Proxy removed")
