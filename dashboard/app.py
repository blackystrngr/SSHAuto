def _manage_network_optimizer(self):
        """Interactive dashboard screen mirroring 3x-ui optimization controls."""
        from features.network_optimizer import NetworkOptimizerFeature
        optimizer = NetworkOptimizerFeature()

        while True:
            ui.clear()
            ui.header("network acceleration hub", "optimize routing latency & bbr layers")
            
            # Read real-time engine status from system paths
            is_active = optimizer.is_installed()
            status_text = "ENABLED & OPTIMIZED" if is_active else "DISABLED (STOCK LINUX)"
            status_color = "\033[1;32m" if is_active else "\033[1;31m"
            
            ui.kv_row("Current Profile Status", f"{status_color}{status_text}")
            
            # Display current active system kernel parameters directly to the panel
            from core.shell import Shell
            cc = Shell.run("sysctl net.ipv4.tcp_congestion_control", capture_output=True, check=False).stdout.strip()
            ss = Shell.run("sysctl net.ipv4.tcp_slow_start_after_idle", capture_output=True, check=False).stdout.strip()
            
            ui.kv_row("Kernel Alg", cc if cc else "Unknown")
            ui.kv_row("Slow Start Config", ss if ss else "Unknown")
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
                ui.header("deploying acceleration parameters...")
                try:
                    optimizer.install()
                    ui.prompt("\nExecution complete. Press Enter to continue...")
                except Exception as e:
                    print(f"\033[1;31mError during network tune: {e}\033[0m")
                    ui.prompt("\nPress Enter to continue...")
            elif action == "2":
                ui.clear()
                ui.header("rolling back kernel overrides...")
                optimizer.remove()
                ui.prompt("\nRollback complete. Press Enter to continue...")
