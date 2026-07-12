"""
Minimal, dependency-free colored logger.

No 'rich', no 'colorama' — just raw ANSI codes, because this program
must run on a bare freshly-booted VPS before pip has installed anything.
"""
from __future__ import annotations

import sys
import time
import threading


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[97m"
    BG_RED = "\033[41m"


class Logger:
    """
    Thread-safe leveled logger with color-coded tags.

    Levels:
        DEBUG    - dim gray, only shown with verbose=True
        INFO     - cyan
        SUCCESS  - green
        WARNING  - yellow
        IMPORTANT- magenta/bold (decisions the operator must notice)
        ERROR    - red
        CRITICAL - red background (fatal, about to abort)
    """

    _lock = threading.Lock()

    def __init__(self, name: str = "sshauto", verbose: bool = False, no_color: bool = False):
        self.name = name
        self.verbose = verbose
        self.no_color = no_color or not sys.stdout.isatty()

    def _paint(self, text: str, *codes: str) -> str:
        if self.no_color:
            return text
        return "".join(codes) + text + Color.RESET

    def _emit(self, tag: str, color: str, message: str, symbol: str):
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            prefix = self._paint(f" {symbol} {tag:<9}", Color.BOLD, color)
            timestamp = self._paint(f"[{ts}]", Color.DIM)
            print(f"{timestamp} {prefix} {message}")

    def debug(self, message: str):
        if self.verbose:
            self._emit("DEBUG", Color.WHITE, self._paint(message, Color.DIM), "•")

    def info(self, message: str):
        self._emit("INFO", Color.CYAN, message, "ℹ")

    def success(self, message: str):
        self._emit("SUCCESS", Color.GREEN, message, "✔")

    def warning(self, message: str):
        self._emit("WARNING", Color.YELLOW, message, "⚠")

    def important(self, message: str):
        self._emit("IMPORTANT", Color.MAGENTA, self._paint(message, Color.BOLD, Color.MAGENTA), "★")

    def error(self, message: str):
        self._emit("ERROR", Color.RED, message, "✖")

    def critical(self, message: str):
        self._emit("CRITICAL", Color.WHITE, self._paint(f" {message} ", Color.BOLD, Color.BG_RED), "☠")

    def rule(self, title: str = ""):
        width = 60
        if self.no_color:
            print("-" * width if not title else f"-- {title} " + "-" * max(0, width - len(title) - 4))
            return
        line = self._paint("─" * width, Color.DIM)
        if title:
            label = self._paint(f" {title} ", Color.BOLD, Color.BLUE)
            print(f"\n{label}{line[:max(0, width - len(title) - 2)]}")
        else:
            print(line)


# Process-wide singleton so every module shares one logger/lock.
log = Logger(verbose="--verbose" in sys.argv or "-v" in sys.argv)
