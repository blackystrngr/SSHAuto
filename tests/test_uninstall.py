from core import uninstall
from core.plugin_manager import PluginManager


def test_every_feature_has_a_remove():
    for name in PluginManager().names():
        assert callable(PluginManager().get(name).remove)


def test_aborts_without_confirmation(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    monkeypatch.setattr(uninstall, "_tunnel_users", lambda: [])
    assert uninstall.run() is False
