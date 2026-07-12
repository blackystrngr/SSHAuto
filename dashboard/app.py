"""
The interactive dashboard, launched by typing `kk` at the shell.
"""
from __future__ import annotations

import subprocess
from core.config import APP_ROOT, state
from core.exceptions import SSHAutoError
from core.logger import log
from core.shell import Shell
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

    # -- screens ----------------------------------------------------------
    def _render_home(self):
        ui.clear()
        ui.header("sshauto dashboard", "type a number, or 'q' to quit")
        stats = self._safe_live_stats()
        if stats:
            ui.kv_row("Active tunnels", str(stats.active_connections))
            ui.kv_row("Total accounts", str(stats.total_users))
            ui.kv_row("Throughput", f"↓ {stats.rx_kbps} kbps   ↑ {stats.tx_kbps} kbps")
        # Show current Git commit (build version)
        commit = self._current_commit()
        print(f"  \033[2mVersion\033[0m  {commit}")
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
            ("10", "Check for updates (git pull & reinstall)"),
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
            "10": self._manual_update,
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

    def _manual_update(self) -> None:
        ui.clear()
        ui.header("Manual Update")
        print("Checking for new commits and applying them...\n")
        result = subprocess.run(
            ["python3", str(APP_ROOT / "scripts/autoupdate_check.py")],
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("\033[31m" + result.stderr + "\033[0m")
        if result.returncode == 0:
            print("\n\033[32m✓ Update completed successfully.\033[0m")
        else:
            print("\n\033[31m✗ Update failed. See logs above.\033[0m")
        ui.pause()

    def _safe_live_stats(self):
        try:
            return self.monitor.live_stats(sample_seconds=0.3)
        except Exception:  # noqa: BLE001 - the home screen must never crash
            return None

    def _current_commit(self) -> str:
        """Return short commit hash, or 'unknown' if not a git repo."""
        git_dir = APP_ROOT / ".git"
        if not git_dir.exists():
            return "unknown"
        result = Shell.run("git rev-parse --short HEAD", check=False)
        return result.stdout.strip() or "unknown"


def main():
    Dashboard().run()


if __name__ == "__main__":
    main()
