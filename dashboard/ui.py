"""
Small, dependency-free terminal UI helpers for the dashboard.
"""
from __future__ import annotations

import os
import shutil

from core.logger import Color


def width() -> int:
    return shutil.get_terminal_size(fallback=(78, 24)).columns


def clear():
    os.system("clear")


def header(title: str, subtitle: str = ""):
    w = min(width(), 78)
    print(f"\033[1;36m{'═' * w}\033[0m")
    print(f"\033[1;97m  {title}\033[0m")
    if subtitle:
        print(f"\033[2m  {subtitle}\033[0m")
    print(f"\033[1;36m{'═' * w}\033[0m")


def menu(options: list[tuple[str, str]]) -> None:
    for key, label in options:
        print(f"  \033[1;33m[{key}]\033[0m  {label}")
    print()


def kv_row(label: str, value: str, color: str = Color.WHITE):
    print(f"  \033[2m{label:<22}\033[0m {color}{value}{Color.RESET}")


def table(headers: list[str], rows: list[list[str]]):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt_row(cells, color=""):
        parts = [f"{str(c):<{widths[i]}}" for i, c in enumerate(cells)]
        return color + "  ".join(parts) + Color.RESET

    print("\033[1m" + fmt_row(headers) + "\033[0m")
    print(Color.DIM + "-" * (sum(widths) + 2 * len(widths)) + Color.RESET)
    if not rows:
        print(Color.DIM + "  (none)" + Color.RESET)
    for row in rows:
        print(fmt_row(row))


def prompt(label: str) -> str:
    return input(f"\033[1;32m› {label}: \033[0m").strip()


def pause():
    input("\033[2mPress Enter to continue...\033[0m")
