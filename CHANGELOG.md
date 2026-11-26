# Changelog

## 1.0.0

### Added
- Optional Squid proxy (`install --squid`, `main.py squid`); authentication always required, default `didntgo` / `didntgo1#`.
- `optional` flag on features; `--version` flag.
- Test suite, ruff, shellcheck, GitHub Actions CI, Makefile, pyproject.

### Fixed
- `datetime.UTC` crash on Python < 3.11.
- `Shell.run(check=False)` raising on timeout (broke `python_proxy` when the `uvloop` pip install was slow).
- `str | None` annotations failing on Python 3.9 in `core/exceptions.py`.
- Squid port was never opened in iptables; Squid removal deleted its config; unchanged config skipped service start.
- `--skip-non-idempotent` selected every feature, including optional ones.
- Bare `except:` and other lint issues.
