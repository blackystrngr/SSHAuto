"""
The interactive dashboard, launched by typing `kk` at the shell.
"""
from __future__ import annotations

from core.config import state
from core.exceptions import SSHAutoError
from core.logger import log
from dashboard import ui
from dashboard.monitor import Monitor
from dashboard.ports import PortManager
from dashboard.users import UserManager


class Dashboard:
    def __init__(self):
        self.users = UserManager()
        self.ports = PortManager()
        self.monitor = Monitor()

    def run(self):
        while True:
            self._render_home()
            choice = ui.prompt("select")
            if choice in ("0", "q", "exit"):
                print("bye.")
                return
            self._dispatch(choice)

    def _render_home(self):
        ui.clear()
        ui.header("sshauto dashboard", "type a number, or 'q' to quit")
        stats = self._safe_live_stats()
        if stats:
            ui.kv_row("Active tunnels", str(stats.active_connections))
            ui.kv_row("Total accounts", str(stats.total_users))
            ui.kv_row("Throughput", f"↓ {stats.rx_kbps} kbps   ↑ {stats.tx_kbps} kbps")
        print()
        ui.menu([
            ("1", "Create SSH/websocket user"),
            ("2", "Delete user"),
            ("3", "List users"),
            ("4", "Live connections / bandwidth"),
            ("5", "Internet speed test"),
            ("6", "Add custom port"),
            ("7", "Remove custom port"),
            ("8", "Show active ports"),
            ("9", "Server status (services)"),
            ("10", "Network Optimizer & BBR Menu"),
            ("11", "Extra Services (Squid, stunnel, sslh, UDPGW)"),
            ("12", "Hysteria2 UDP/QUIC tunnel"),
            ("13", "DNS tunnel (iodine)"),
            ("14", "ICMP tunnel (pingtunnel)"),
            ("15", "AdBlock DNS (ads/trackers/malware/miners)"),
            ("0", "Exit"),
        ])

    def _dispatch(self, choice: str):
        actions = {
            "1": self._create_user,
            "2": self._delete_user,
            "3": self._list_users,
            "4": self._live_view,
            "5": self._speed_test,
            "6": lambda: self._add_port(),
            "7": lambda: self._remove_port(),
            "8": self._show_ports,
            "9": self._service_status,
            "10": self._manage_network_optimizer,
            "11": self._extra_services_status,
            "12": self._manage_hysteria2,
            "13": self._manage_dns_tunnel,
            "14": self._manage_icmp_tunnel,
            "15": self._manage_adblock,
        }
        action = actions.get(choice)
        if not action:
            log.warning(f"no such option: {choice}")
            ui.pause()
            return
        try:
            action()
        except SSHAutoError as exc:
            log.error(exc.message)
            if exc.hint:
                log.warning(exc.hint)
        except KeyboardInterrupt:
            pass
        ui.pause()

    def _create_user(self):
        ui.clear()
        ui.header("create user")
        username = ui.prompt("username")
        password = ui.prompt("password")
        expiry_raw = ui.prompt("expires in days [30]") or "30"
        expiry = int(expiry_raw) if expiry_raw.isdigit() else 30
        try:
            user = self.users.create(username, password, expiry)
            log.success(f"User '{user.username}' created successfully.")
            print()
            ui.kv_row("Username", user.username, color="\033[1;32m")
            ui.kv_row("Password", password, color="\033[1;32m")
            ui.kv_row("Expires", user.expires)
            print()
            log.important("Use these credentials in your SSH client (WebSocket mode).")
        except Exception as e:
            log.error(f"Failed to create user: {e}")
        ui.pause()

    def _delete_user(self):
        ui.clear()
        ui.header("delete user")
        username = ui.prompt("username to delete")
        self.users.delete(username)

    def _list_users(self):
        ui.clear()
        ui.header("users", f"group: sshauto-users")
        rows = [[u.username, u.expires, "locked" if u.locked else "active"]
                for u in self.users.list()]
        ui.table(["username", "expires", "status"], rows)

    def _live_view(self):
        ui.clear()
        ui.header("live connections")
        stats = self.monitor.live_stats(sample_seconds=1.0)
        ui.kv_row("Active tunnels", str(stats.active_connections))
        ui.kv_row("Total accounts", str(stats.total_users))
        ui.kv_row("Download", f"{stats.rx_kbps} kbps")
        ui.kv_row("Upload", f"{stats.tx_kbps} kbps")

    def _speed_test(self):
        ui.clear()
        ui.header("internet speed test", "downloading 25MB test file...")
        mbps = self.monitor.speed_test()
        if mbps is None:
            log.error("speed test failed (no connectivity or curl error)")
        else:
            ui.kv_row("Download speed", f"{mbps} Mbps")

    def _add_port(self):
        ui.clear()
        ui.header("add custom port")
        kind = ui.prompt("type (http/https)").lower()
        port = int(ui.prompt("port number"))
        self.ports.add(port, kind)

    def _remove_port(self):
        ui.clear()
        ui.header("remove custom port")
        kind = ui.prompt("type (http/https)").lower()
        port = int(ui.prompt("port number"))
        self.ports.remove(port, kind)

    def _show_ports(self):
        ui.clear()
        ui.header("active relay ports")
        all_ports = self.ports.list_all()
        ui.kv_row("HTTP", ", ".join(map(str, all_ports["http"])))
        ui.kv_row("HTTPS", ", ".join(map(str, all_ports["https"])))
        data = state.load()
        ui.kv_row("Dropbear backend", f"127.0.0.1:{data.get('dropbear_port')}")
        ui.kv_row("SSH direct port", str(data.get("ssh_port")))
        ui.kv_row("Squid proxy", "127.0.0.1:3128")
        ui.kv_row("UDP Gateway", "0.0.0.0:7300 (public)")

    def _service_status(self):
        ui.clear()
        ui.header("service status")
        from core.plugin_manager import PluginManager
        PluginManager().status_all()

    def _manage_network_optimizer(self):
        try:
            from features.network_optimizer import NetworkOptimizerFeature
            optimizer = NetworkOptimizerFeature()
        except ImportError:
            log.error("NetworkOptimizerFeature module not found")
            return

        while True:
            ui.clear()
            ui.header("network acceleration hub", "optimize routing latency & bbr layers")

            is_active = optimizer.is_installed()
            data = state.load()
            bbr_enabled = data.get("enable_bbr", True)

            status_text = "ENABLED & OPTIMIZED" if is_active else "DISABLED (STOCK LINUX)"
            status_color = "\033[1;32m" if is_active else "\033[1;31m"
            ui.kv_row("Current Profile Status", f"{status_color}{status_text}\033[0m")
            ui.kv_row("BBR Congestion Control", f"{'✅ ON' if bbr_enabled else '❌ OFF'}")

            from core.shell import Shell
            cc_res = Shell.run("sysctl net.ipv4.tcp_congestion_control", check=False)
            ss_res = Shell.run("sysctl net.ipv4.tcp_slow_start_after_idle", check=False)
            cc = cc_res.stdout.strip() if cc_res.ok else "Unknown"
            ss = ss_res.stdout.strip() if ss_res.ok else "Unknown"
            ui.kv_row("Kernel Alg", cc)
            ui.kv_row("Slow Start Config", ss)
            print()
            ui.menu([
                ("1", "Apply Extreme Low-Latency Profile + BBR (3x-ui Optimization)"),
                ("2", "Remove Optimizations (Reset to OS Default)"),
                ("3", f"Toggle BBR ({'ON' if bbr_enabled else 'OFF'})"),
                ("0", "Back to Main Menu")
            ])

            action = ui.prompt("Select action")
            if action == "0" or not action:
                return
            elif action == "1":
                ui.clear()
                ui.header("deploying acceleration parameters")
                try:
                    state.set("enable_bbr", True)
                    optimizer.install()
                    ui.prompt("\nExecution complete. Press Enter to continue...")
                except Exception as e:
                    log.error(f"Error during network tune: {e}")
                    ui.prompt("\nPress Enter to continue...")
            elif action == "2":
                ui.clear()
                ui.header("rolling back kernel overrides")
                try:
                    optimizer.remove()
                    ui.prompt("\nRollback complete. Press Enter to continue...")
                except Exception as e:
                    log.error(f"Error during rollback: {e}")
                    ui.prompt("\nPress Enter to continue...")
            elif action == "3":
                new_state = not bbr_enabled
                state.set("enable_bbr", new_state)
                log.info(f"BBR toggled to {'ON' if new_state else 'OFF'}. Reapplying optimizer...")
                optimizer.install()
                ui.prompt(f"\nBBR is now {'ENABLED' if new_state else 'DISABLED'}. Press Enter to continue...")

    def _extra_services_status(self):
        ui.clear()
        ui.header("Extra Services Overview", "Squid, stunnel, sslh, UDPGW")

        from core.shell import Shell
        from pathlib import Path

        squid_active = Shell.run("systemctl is-active squid", check=False).ok
        squid_installed = Path("/etc/squid/squid.conf").exists()
        ui.kv_row("Squid HTTP Proxy",
                  f"{'✅ ACTIVE' if squid_active else '❌ INACTIVE'} (port 3128 internal)",
                  color="\033[1;32m" if squid_active else "\033[1;31m")

        stunnel_active = Shell.run("systemctl is-active stunnel4", check=False).ok
        stunnel_installed = Path("/etc/stunnel/stunnel.conf").exists()
        ui.kv_row("stunnel SSL Tunnel",
                  f"{'✅ ACTIVE' if stunnel_active else '❌ INACTIVE'} (port 4443 internal)",
                  color="\033[1;32m" if stunnel_active else "\033[1;31m")

        sslh_active = Shell.run("systemctl is-active sslh", check=False).ok
        sslh_installed = Path("/etc/default/sslh").exists()
        ui.kv_row("sslh TLS Demuxer",
                  f"{'✅ ACTIVE' if sslh_active else '❌ INACTIVE'} (port 443)",
                  color="\033[1;32m" if sslh_active else "\033[1;31m")

        udpgw_active = Shell.run("systemctl is-active badvpn-udpgw", check=False).ok
        udpgw_installed = Path("/usr/local/bin/badvpn-udpgw").exists()
        ui.kv_row("badvpn-udpgw (UDP)",
                  f"{'✅ ACTIVE' if udpgw_active else '❌ INACTIVE'} (port 7300 public)",
                  color="\033[1;32m" if udpgw_active else "\033[1;31m")

        print()
        ui.kv_row("Port 443", "sslh forwards → nginx:8443 (HTTPS/WS) and stunnel:4443 (raw SSL)")
        ui.kv_row("Port 80/8080/8880", "nginx splits → WebSocket (Python proxy) and plain HTTP (Squid)")
        print()
        log.important("Use 'sudo python3 main.py install --only <feature>' to enable/disable individual services.")

    def _manage_hysteria2(self):
        from features.hysteria2 import Hysteria2Feature
        self._toggle_feature(Hysteria2Feature())

    def _manage_dns_tunnel(self):
        from features.dns_tunnel import DnsTunnelFeature
        self._toggle_feature(DnsTunnelFeature())

    def _manage_icmp_tunnel(self):
        from features.icmp_tunnel import IcmpTunnelFeature
        self._toggle_feature(IcmpTunnelFeature())

    def _toggle_feature(self, feature):
        ui.clear()
        ui.header(f"Manage {feature.name}")
        status = "Active" if feature.is_installed() else "Inactive"
        ui.kv_row("Status", status)
        print()
        ui.menu([
            ("1", "Install/Enable"),
            ("2", "Remove/Disable"),
            ("0", "Back"),
        ])
        choice = ui.prompt("Select")
        if choice == "1":
            try:
                feature.install()
                log.success(f"{feature.name} enabled.")
            except Exception as e:
                log.error(f"Failed: {e}")
        elif choice == "2":
            feature.remove()
            log.success(f"{feature.name} disabled.")
        ui.pause()

    def _safe_live_stats(self):
        try:
            return self.monitor.live_stats(sample_seconds=0.3)
        except Exception:
            return None

    def _manage_adblock(self):
        from dashboard.adblock import adblock_menu
        adblock_menu()


def main():
    Dashboard().run()


if __name__ == "__main__":
    main()
