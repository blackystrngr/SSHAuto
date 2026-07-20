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
    _lock = threading.Lock()
    def __init__(self, name="sshauto", verbose=False, no_color=False):
        self.name = name
        self.verbose = verbose
        self.no_color = no_color or not sys.stdout.isatty()

    def _paint(self, text, *codes):
        if self.no_color:
            return text
        return "".join(codes) + text + Color.RESET

    def _emit(self, tag, color, message, symbol):
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            prefix = self._paint(f" {symbol} {tag:<9}", Color.BOLD, color)
            timestamp = self._paint(f"[{ts}]", Color.DIM)
            print(f"{timestamp} {prefix} {message}")

    def debug(self, msg):
        if self.verbose:
            self._emit("DEBUG", Color.WHITE, self._paint(msg, Color.DIM), "•")
    def info(self, msg):   self._emit("INFO", Color.CYAN, msg, "ℹ")
    def success(self, msg): self._emit("SUCCESS", Color.GREEN, msg, "✔")
    def warning(self, msg): self._emit("WARNING", Color.YELLOW, msg, "⚠")
    def important(self, msg): self._emit("IMPORTANT", Color.MAGENTA, self._paint(msg, Color.BOLD, Color.MAGENTA), "★")
    def error(self, msg):  self._emit("ERROR", Color.RED, msg, "✖")
    def critical(self, msg): self._emit("CRITICAL", Color.WHITE, self._paint(f" {msg} ", Color.BOLD, Color.BG_RED), "☠")
    def rule(self, title=""):
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

log = Logger(verbose="--verbose" in sys.argv or "-v" in sys.argv)
