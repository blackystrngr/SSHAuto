# Contributing

1. `pip install -r requirements-dev.txt`
2. Make your change; add a test in `tests/` when it is testable without root.
3. `make check` must pass (ruff, shellcheck, pytest). CI runs Python 3.9, 3.11 and 3.13.

Rules of the project:

- Supported targets are Debian and Ubuntu only; code must run on Python 3.9+.
- Never touch `/etc/resolv.conf` or systemd-resolved.
- No ACME/Let's Encrypt fallback and no auto-update.
- Features must be idempotent and live in `features/` as `BaseFeature` subclasses.
