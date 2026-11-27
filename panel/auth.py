"""Password hashing, signed session cookies and login rate limiting. Standard library only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque

from core.config import state

SESSION_TTL = 12 * 3600
MAX_FAILURES = 5          # per client IP ...
WINDOW = 300              # ... within this many seconds
GLOBAL_MAX_FAILURES = 40  # across all IPs, same window


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        probe = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(probe, _unb64(digest))
    except (ValueError, TypeError):
        return False


def generate_password(length: int = 14) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no look-alikes
    return "".join(secrets.choice(alphabet) for _ in range(length))


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._by_ip: dict[str, deque] = {}
        self._all: deque = deque()

    def _prune(self, q: deque, now: float) -> None:
        while q and now - q[0] > WINDOW:
            q.popleft()

    def blocked(self, ip: str) -> int:
        """Seconds until the client may try again (0 = allowed)."""
        now = self._clock()
        with self._lock:
            q = self._by_ip.setdefault(ip, deque())
            self._prune(q, now)
            self._prune(self._all, now)
            if len(q) >= MAX_FAILURES:
                return int(WINDOW - (now - q[0])) + 1
            if len(self._all) >= GLOBAL_MAX_FAILURES:
                return int(WINDOW - (now - self._all[0])) + 1
            return 0

    def fail(self, ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._by_ip.setdefault(ip, deque()).append(now)
            self._all.append(now)

    def success(self, ip: str) -> None:
        with self._lock:
            self._by_ip.pop(ip, None)


class Auth:
    """Single administrator. Credentials live in the state store (hash only, never the password)."""

    def __init__(self, store=state):
        self.store = store

    def configured(self) -> bool:
        data = self.store.load()
        return bool(data.get("panel_user") and data.get("panel_hash") and data.get("panel_secret"))

    def set_credentials(self, user: str, password: str) -> None:
        data = self.store.load()
        data["panel_user"] = user
        data["panel_hash"] = hash_password(password)
        data["panel_secret"] = secrets.token_hex(32)     # rotating the secret signs everyone out
        self.store.save(data)

    def check(self, user: str, password: str) -> bool:
        data = self.store.load()
        stored = data.get("panel_hash") or hash_password(secrets.token_hex(8))   # equal work if unconfigured
        ok_pass = verify_password(password, stored)
        ok_user = hmac.compare_digest(str(user).encode(), str(data.get("panel_user", "")).encode())
        return bool(ok_pass and ok_user and self.configured())

    def issue(self, user: str, now: float | None = None) -> str:
        secret = self.store.get("panel_secret", "")
        payload = _b64(json.dumps({"u": user, "e": int((now or time.time()) + SESSION_TTL),
                                   "n": secrets.token_hex(4)}).encode())
        sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}"

    def verify(self, token: str | None, now: float | None = None) -> str | None:
        if not token or "." not in token:
            return None
        data = self.store.load()
        secret = data.get("panel_secret", "")
        if not secret:
            return None
        payload, _, sig = token.partition(".")
        good = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        try:
            body = json.loads(_unb64(payload))
        except ValueError:
            return None
        if body.get("e", 0) < (now or time.time()):
            return None
        return body.get("u") if body.get("u") == data.get("panel_user") else None
