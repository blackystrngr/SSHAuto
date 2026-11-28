from core.plugin_manager import PluginManager


def test_squid_is_optional():
    m = PluginManager()
    assert "squid_proxy" in m.names()
    assert "squid_proxy" not in m.default_names()


def test_dependencies_install_first():
    order = [f.name for f in PluginManager()._ordered()]
    for name in order:
        cls = PluginManager()._classes[name]
        for dep in cls.depends_on:
            assert order.index(dep) < order.index(name)


def test_explicit_squid_pulls_dependencies():
    m = PluginManager()
    order = [f.name for f in m._ordered(["squid_proxy"])]
    assert order[-1] == "squid_proxy"
    assert "packages" in set(order)


def test_dependents_skipped_and_failures_retried(monkeypatch):
    import core.plugin_manager as pm
    from features.base import BaseFeature

    calls = {"a": 0, "b": 0}

    class A(BaseFeature):
        name = "a"
        depends_on = []
        def is_installed(self): return False
        def install(self): calls["a"] += 1; raise RuntimeError("boom")
        def remove(self): pass

    class B(BaseFeature):
        name = "b"
        depends_on = ["a"]
        def is_installed(self): return False
        def install(self): calls["b"] += 1
        def remove(self): pass

    monkeypatch.setattr(pm.time, "sleep", lambda *_: None)
    m = pm.PluginManager()
    m._classes = {"a": A, "b": B}
    results = m.install_all()
    assert results == {"a": False, "b": False}
    assert calls == {"a": 2, "b": 0}   # a retried once, b never attempted


def test_firewall_first_and_panel_last():
    order = [f.name for f in PluginManager()._ordered(["web_panel", "firewall", "squid_proxy", "fail2ban_service", "udpgw_service"])]
    assert order[0] == "firewall"
    assert order[-1] == "web_panel"
