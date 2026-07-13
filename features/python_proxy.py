import os
import json
from pathlib import Path
from features.base import BaseFeature
from core.shell import Shell
from core.logger import log

class PythonProxyFeature(BaseFeature):
    name = "python_proxy"
    description = "Universal Auto-Detecting WebSocket Proxy with SSH Hardening"
    depends_on = ["packages"]

    def is_installed(self) -> bool:
        return os.path.exists("/opt/sshauto/ws_proxy.py") and \
               Shell.run("systemctl is-active sshauto-proxy", check=False).ok

    def _harden_ssh_server(self) -> None:
        """Safely configures sshd_config thresholds to maximize connection stability."""
        sshd_config = "/etc/ssh/sshd_config"
        if os.path.exists(sshd_config):
            log.info("Applying server-side multiplexing and connection threshold tuning...")
            with open(sshd_config, "r") as f:
                lines = f.readlines()
            
            with open(sshd_config, "w") as f:
                for line in lines:
                    if "MaxSessions" not in line and "MaxStartups" not in line:
                        f.write(line)
                f.write("\n# Optimized for WebSocket Tunneling / Multiplexing\n")
                f.write("MaxSessions 100\n")
                f.write("MaxStartups 100:30:200\n")
            
            Shell.run("systemctl restart ssh", check=False)
            log.info("SSH thresholds successfully adjusted.")
        else:
            log.warning(f"Could not locate {sshd_config}. Skipping SSH server tuning.")

    def install(self) -> None:
        log.info("Installing/Updating Python Proxy service...")
        
        # 1. Enforce Server High-Concurrency Tuning
        self._harden_ssh_server()
        
        # 2. Write out the robust Auto-Detecting Proxy core
        script_content = r'''
import asyncio
import hashlib
import base64
import json
from pathlib import Path

# Runtime Path References aligned with config.py
STATE_PATH = Path("/var/lib/sshauto/state.json")
BACKEND_IP = "127.0.0.1"
DROPBEAR_PORT_DEFAULT = 113  # Sourced directly from system configuration defaults
LISTEN_PORT = 8000

def get_live_dropbear_port():
    """Reads state.json dynamically to follow the current operational port."""
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text())
            return data.get("dropbear_port", DROPBEAR_PORT_DEFAULT)
    except Exception:
        pass
    return DROPBEAR_PORT_DEFAULT

async def handle(client_reader, client_writer):
    backend_writer = None
    try:
        header_data = b""
        # 1. Capture HTTP request headers safely without blocking
        while b"\r\n\r\n" not in header_data and len(header_data) < 4096:
            chunk = await client_reader.read(1024)
            if not chunk:
                break
            header_data += chunk

        if not header_data or b"\r\n\r\n" not in header_data:
            return

        # 2. Separate trailing data from headers to prevent handshake losses
        idx = header_data.find(b"\r\n\r\n")
        trailing = header_data[idx+4:]

        # 3. Parse Sec-WebSocket-Key for standards compliance
        ws_key = None
        lines = header_data[:idx].decode("utf-8", errors="ignore").split("\r\n")
        for line in lines:
            if line.lower().startswith("sec-websocket-key:"):
                ws_key = line.split(":", 1)[1].strip()
                break

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
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n\r\n"
            ).encode()

        client_writer.write(response)
        await client_writer.drain()

        # 4. Open Backend Connection to Dropbear
        backend_port = get_live_dropbear_port()
        backend_reader, backend_writer = await asyncio.open_connection(BACKEND_IP, backend_port)

        # 5. Populate trailing buffer if initial read didn't lock any bytes
        if not trailing:
            try:
                chunk = await client_reader.read(4096)
                if not chunk:
                    return
                trailing = chunk
            except Exception:
                return

        # 6. Auto-Detect Mode: Standard WS Frames vs Raw Direct Custom Injections
        is_ws_framed = len(trailing) > 0 and trailing[0] in (0x81, 0x82)

        if not is_ws_framed:
            # MODE A: Raw Payloads (Direct / HTTP Custom injects)
            if trailing:
                backend_writer.write(trailing)
                await backend_writer.drain()

            async def pipe_raw(reader, writer):
                while True:
                    data = await reader.read(16384)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()

            await asyncio.gather(
                pipe_raw(client_reader, backend_writer),
                pipe_raw(backend_reader, client_writer)
            )
        else:
            # MODE B: Compliant WS Framing (CDN Tunnels / Strict Frameworks)
            async def client_to_backend_ws(reader, writer, initial_buf):
                buffer = initial_buf
                while True:
                    while len(buffer) < 2:
                        chunk = await reader.read(16384)
                        if not chunk:
                            break
                        buffer += chunk
                    if len(buffer) < 2:
                        break

                    b1, b2 = buffer[0], buffer[1]
                    masked = b2 & 0x80
                    payload_len = b2 & 0x7F
                    header_len = 2

                    if payload_len == 126:
                        while len(buffer) < header_len + 2:
                            chunk = await reader.read(16384)
                            if not chunk:
                                break
                            buffer += chunk
                        if len(buffer) < header_len + 2:
                            break
                        payload_len = int.from_bytes(buffer[header_len:header_len+2], 'big')
                        header_len += 2
                    elif payload_len == 127:
                        while len(buffer) < header_len + 8:
                            chunk = await reader.read(16384)
                            if not chunk:
                                break
                            buffer += chunk
                        if len(buffer) < header_len + 8:
                            break
                        payload_len = int.from_bytes(buffer[header_len:header_len+8], 'big')
                        header_len += 8

                    if masked:
                        while len(buffer) < header_len + 4:
                            chunk = await reader.read(16384)
                            if not chunk:
                                break
                            buffer += chunk
                        if len(buffer) < header_len + 4:
                            break
                        mask_key = buffer[header_len:header_len+4]
                        header_len += 4

                    while len(buffer) < header_len + payload_len:
                        chunk = await reader.read(16384)
                        if not chunk:
                            break
                        buffer += chunk
                    if len(buffer) < header_len + payload_len:
                        break

                    payload = buffer[header_len:header_len+payload_len]
                    buffer = buffer[header_len+payload_len:]

                    if masked:
                        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

                    if payload:
                        writer.write(payload)
                        await writer.drain()

            async def backend_to_client_ws(reader, writer):
                while True:
                    data = await reader.read(16384)
                    if not data:
                        break
                    b1 = 0x80 | 0x02  # Mark as FIN + Binary Frame Type
                    p_len = len(data)
                    if p_len <= 125:
                        header = bytes([b1, p_len])
                    elif p_len <= 65535:
                        header = bytes([b1, 126]) + p_len.to_bytes(2, 'big')
                    else:
                        header = bytes([b1, 127]) + p_len.to_bytes(8, 'big')
                    writer.write(header + data)
                    await writer.drain()

            await asyncio.gather(
                client_to_backend_ws(client_reader, backend_writer, trailing),
                backend_to_client_ws(backend_reader, client_writer)
            )

    except Exception:
        pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except:
            pass
        if backend_writer:
            try:
                backend_writer.close()
                await backend_writer.wait_closed()
            except:
                pass
'''
        os.makedirs("/opt/sshauto", exist_ok=True)
        with open("/opt/sshauto/ws_proxy.py", "w") as f:
            f.write(script_content.strip() + "\n")

        # 3. Create Systemd Service Descriptor Unit
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
        Shell.run("systemctl restart sshauto-proxy")
        
        # 4. STRICT ENFORCEMENT CHECKPOINT
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
