from __future__ import annotations

from core.config import state
from core.exceptions import SSHAutoError
from core.logger import log
from dashboard import ui
from dashboard.monitor import Monitor
from dashboard.ports import PortManager
from dashboard.users import UserManager
# Import the new feature
from features.multiplexing import MultiplexingFeature


class Dashboard:
    def __init__(self):
        self.users = UserManager()
        self.ports = PortManager()
        self.monitor = Monitor()
        self.multiplex = MultiplexingFeature()

    def run(self):
        while True:
            self._render_home()
            choice = ui.prompt("select")
            if choice in ("0", "q", "exit"):
                print("bye.")
                return
            self._dispatch(choice)

    # -- screens ----------------------------------------------------------
    def _render_home(self):
        ui.clear()
        ui.header("sshauto dashboard", "type a number, or 'q' to quit")
        stats = self._safe_live_stats()
        if stats:
            ui.kv_row("Active tunnels", str(stats.active_connections))
            ui.kv_row("Total accounts", str(stats.total_users))
            ui.kv_row("Throughput", f"↓ {stats.rx_kbps} kbps    ↑ {stats.tx_kbps} kbps")
        
        # Check status for the menu label
        mux_status = "ON" if self.multiplex.is_installed() else "OFF"
        
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
            ("11", f"Toggle Multiplexing [{mux_status}]"),
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
            "11": self._toggle_multiplexing,
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

    # -- actions ------------------------------------------------------
    def _create_user(self):
        ui.clear()
        ui.header("create user")
        username = ui.prompt("username")
        password = ui.prompt("password")
        expiry_raw = ui.prompt("expires in days [30]") or "30"
        expiry = int(expiry_raw) if expiry_raw.isdigit() else 30
        self.users.create(username, password, expiry)

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

    def _service_status(self):
        ui.clear()
        ui.header("service status")
        from core.plugin_manager import PluginManager
        PluginManager().status_all()

    def _toggle_multiplexing(self):
        ui.clear()
        ui.header("multiplexing settings")
        if self.multiplex.is_installed():
            log.info("Disabling Multiplexing...")
            self.multiplex.remove()
        else:
            log.info("Enabling Multiplexing...")
            self.multiplex.install()

    def _manage_network_optimizer(self):
        """Interactive dashboard screen mirroring 3x-ui optimization controls."""
        try:
            from features.network_optimizer import NetworkOptimizerFeature
            optimizer = NetworkOptimizerFeature()
        except ImportError:
            from core.logger import log
            log.error("NetworkOptimizerFeature module not found at features/network_optimizer.py")
            return

        while True:
            from dashboard import ui
            ui.clear()
            ui.header("network acceleration hub", "optimize routing latency & bbr layers")
            
            is_active = optimizer.is_installed()
            status_text = "ENABLED & OPTIMIZED" if is_active else "DISABLED (STOCK LINUX)"
            status_color = "\033[1;32m" if is_active else "\033[1;31m"
            
            ui.kv_row("Current Profile Status", f"{status_color}{status_text}\033[0m")
            
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
                ("0", "Back to Main Menu")
            ])
            
            action = ui.prompt("Select action")
            if action == "0" or not action:
                return
            elif action == "1":
                ui.clear()
                ui.header("deploying acceleration parameters")
                try:
                    optimizer.install()
                    ui.prompt("\nExecution complete. Press Enter to continue...")
                except Exception as e:
                    from core.logger import log
                    log.error(f"Error during network tune: {e}")
                    ui.prompt("\nPress Enter to continue...")
            elif action == "2":
                ui.clear()
                ui.header("rolling back kernel overrides")
                try:
                    optimizer.remove()
                    ui.prompt("\nRollback complete. Press Enter to continue...")
                except Exception as e:
                    from core.logger import log
                    log.error(f"Error during rollback: {e}")
                    ui.prompt("\nPress Enter to continue...")
                    
    def _safe_live_stats(self):
        try:
            return self.monitor.live_stats(sample_seconds=0.3)
        except Exception:
            return None


def main():
    Dashboard().run()


if __name__ == "__main__":
    main()
