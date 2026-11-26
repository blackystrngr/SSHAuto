from pathlib import Path

from core.config import StateStore


def test_defaults_and_roundtrip(tmp_path: Path):
    s = StateStore(tmp_path / "state.json")
    data = s.ensure_defaults()
    assert data["squid_user"] == "didntgo"
    s.set("ssh_port", 2200)
    assert s.get("ssh_port") == 2200
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600


def test_corrupt_state_is_ignored(tmp_path: Path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert StateStore(p).load() == {}
