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
    assert {"packages", "firewall"} <= set(order)
