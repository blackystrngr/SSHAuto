# Changelog

## 1.0.0

### Added
- Optional Squid proxy (`install --squid`, `main.py squid`); authentication always required, default `didntgo` / `didntgo1#`.
- `optional` flag on features; `--version` flag.
- Test suite, ruff, shellcheck, GitHub Actions CI, Makefile, pyproject.

### Added (self-healing)
- `main.py doctor`: tests services, Dropbear loopback binding, and a real WebSocket handshake through the proxy and through nginx (expects `101` + SSH banner); reinstalls broken features and re-checks. `--no-heal` to only check.
- `install` runs `doctor` automatically at the end and exits non-zero if the tunnel is not working.
- Features whose dependency failed are skipped (e.g. nginx no longer configured against a missing proxy); idempotent features are retried once.

### Changed
- Python proxy now listens on `127.0.0.1` only; nginx is the sole public listener.
- uvloop is mandatory (installer tries pip, then apt, and fails the step if it can't be installed).
- Proxy tuning: 64 KB reads, 256 KB stream buffers, backlog 1024.

### Fixed
- Distro `dropbear.service` was left enabled and grabbed port 110 on 0.0.0.0, so `dropbear-tunnel` (127.0.0.1) failed with "Address already in use". It is now disabled and masked, and `NO_START=1` is written.
- Proxy no longer drops a connection on a malformed header line.
- `datetime.UTC` crash on Python < 3.11.
- `Shell.run(check=False)` raising on timeout (broke `python_proxy` when the `uvloop` pip install was slow).
- `str | None` annotations failing on Python 3.9 in `core/exceptions.py`.
- Squid port was never opened in iptables; Squid removal deleted its config; unchanged config skipped service start.
- `--skip-non-idempotent` selected every feature, including optional ones.
- Bare `except:` and other lint issues.
