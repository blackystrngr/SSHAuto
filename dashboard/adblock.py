"""
dashboard/adblock.py

`kk` submenu for the adblock_dns feature: toggle, refresh, whitelist management, and stats.
"""
from dashboard import ui
from core.logger import log
from features.adblock_dns import AdBlockDNSFeature


def adblock_menu() -> None:
    feature = AdBlockDNSFeature()

    while True:
        installed = feature.is_installed()
        stats = feature.stats() if installed else {}
        status = "ENABLED" if installed else "DISABLED"
        status_color = "\033[1;32m" if installed else "\033[1;31m"

        ui.clear()
        ui.header(f"Ad / Tracker / Malware / Miner Blocking — {status_color}{status}\033[0m")

        if installed:
            ui.kv_row("Domains blocked", str(stats.get("total_blocked", 0)))
            ui.kv_row("Whitelisted", str(stats.get("whitelisted", 0)))
        print()

        ui.menu([
            ("1", "Enable" if not installed else "Disable"),
            ("2", "Force blocklist refresh"),
            ("3", "Add domain to whitelist"),
            ("4", "Remove domain from whitelist"),
            ("0", "Back"),
        ])

        choice = ui.prompt("Select")

        if choice == "1":
            if installed:
                feature.remove()
                log.success("Ad blocking disabled.")
            else:
                feature.install()
                log.success("Ad blocking enabled.")
        elif choice == "2":
            if not installed:
                log.warning("Enable the feature first.")
                ui.pause()
                continue
            stats = feature.refresh_blocklist()
            log.success(f"Refreshed. {stats['total_blocked']} domains now blocked.")
        elif choice == "3":
            if not installed:
                log.warning("Enable the feature first.")
                ui.pause()
                continue
            domain = ui.prompt("Domain to whitelist")
            feature.whitelist_add(domain)
            log.success(f"{domain} whitelisted and blocklist refreshed.")
        elif choice == "4":
            if not installed:
                log.warning("Enable the feature first.")
                ui.pause()
                continue
            domain = ui.prompt("Domain to remove from whitelist")
            feature.whitelist_remove(domain)
            log.success(f"{domain} removed from whitelist.")
        elif choice == "0":
            return

        ui.pause()
