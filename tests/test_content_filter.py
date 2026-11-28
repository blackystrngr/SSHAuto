import argparse

import pytest

import main
from core.config import StateStore
from core.exceptions import ValidationError
from features.content_filter import (
    ContentFilterFeature,
    render_dnsmasq_conf,
    render_dropin,
    render_resolv_conf,
    render_unit,
)


def test_feature_is_optional_and_depends_on_tunnel():
    f = ContentFilterFeature
    assert f.optional and {"dropbear_service", "python_proxy"} <= set(f.depends_on)


def test_dnsmasq_conf_is_loopback_only_and_keeps_system_dns():
    conf = render_dnsmasq_conf()
    assert "listen-address=127.0.0.153" in conf and "bind-interfaces" in conf
    assert "conf-dir=/etc/sshauto/filter/lists,*.conf" in conf
    assert "no-resolv" not in conf and "resolv-file=/etc/resolv.conf" in conf  # upstream = system default


def test_dropin_binds_resolver_only_for_the_service():
    d = render_dropin(False)
    assert "BindReadOnlyPaths=-/etc/sshauto/filter/resolv.conf:/etc/resolv.conf" in d
    assert "IPAddressDeny" not in d
    p = render_dropin(True)
    assert "IPAddressDeny=10.0.0.0/8" in p and "169.254.0.0/16" in p
    assert "127.0.0.0/8" not in p           # loopback must stay reachable (nginx -> proxy -> dropbear)


def test_unit_runs_dnsmasq_in_foreground():
    assert "-k --conf-file=/etc/sshauto/filter/dnsmasq.conf" in render_unit("/usr/sbin/dnsmasq")


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    s = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(main, "state", s)
    return s


def _args(**kw):
    base = dict(categories=None, allow=None, block_private=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_categories_parsing(tmp_state):
    main._apply_filter_args(_args(categories="ads, malware"))
    assert tmp_state.get("filter_categories") == ["ads", "malware"]
    main._apply_filter_args(_args(categories="all"))
    assert tmp_state.get("filter_categories") == ["ads", "malware", "phishing", "cryptominers"]
    main._apply_filter_args(_args(categories="none"))
    assert tmp_state.get("filter_categories") == []


def test_unknown_category_rejected(tmp_state):
    with pytest.raises(ValidationError):
        main._apply_filter_args(_args(categories="ads,porn"))


def test_allow_and_private_flags(tmp_state):
    main._apply_filter_args(_args(allow="Example.com, b.org", block_private=True))
    assert tmp_state.get("filter_allow") == ["example.com", "b.org"]
    assert tmp_state.get("filter_private") is True
    main._apply_filter_args(_args())      # untouched when not given
    assert tmp_state.get("filter_private") is True


def test_resolv_conf_filter_first_host_nameservers_as_fallback():
    host = "# stub\nnameserver 127.0.0.53\nnameserver 127.0.0.153\nnameserver 127.0.0.53\nsearch example\n"
    out = render_resolv_conf(host).splitlines()
    assert out[1] == "nameserver 127.0.0.153" and out[2] == "nameserver 127.0.0.53"
    assert out.count("nameserver 127.0.0.153") == 1 and "options timeout:1 attempts:2" in out
    assert render_resolv_conf("").count("nameserver") == 1      # no host file: filter only


def test_filter_never_writes_system_dns_files():
    """The filter may only READ /etc/resolv.conf; nothing may write or manage system DNS."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("features/content_filter.py", "core/blocklists.py", "core/doctor.py", "install.sh"):
        text = "\n".join(ln.split("#", 1)[0] for ln in (root / rel).read_text().splitlines())   # code only, no comments
        for bad in ("systemd-resolved", "resolvectl", "netplan", "dhclient", "chattr", "/etc/systemd/resolved"):
            assert bad not in text, (rel, bad)
    cf = (root / "features/content_filter.py").read_text()
    assert "HOST_RESOLV.write" not in cf and 'Path("/etc/resolv.conf").write' not in cf


def test_filter_is_not_installed_by_default():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    sh = (root / "install.sh").read_text()
    assert 'FILTER="${SSHAUTO_FILTER:-0}"' in sh and '"$FILTER" == "1" ]] && args+=(--filter)' in sh
    assert ContentFilterFeature.optional
    assert "content_filter" not in __import__("core.plugin_manager", fromlist=["x"]).PluginManager().default_names()
