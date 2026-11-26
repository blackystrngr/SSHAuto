import argparse

import pytest

import main
from core.config import StateStore
from core.exceptions import ValidationError
from features.content_filter import (
    ContentFilterFeature,
    render_dnsmasq_conf,
    render_dropin,
    render_unit,
)


def test_feature_is_optional_and_depends_on_tunnel():
    f = ContentFilterFeature
    assert f.optional and {"dropbear_service", "python_proxy"} <= set(f.depends_on)


def test_dnsmasq_conf_is_loopback_only_and_keeps_system_dns():
    conf = render_dnsmasq_conf()
    assert "listen-address=127.0.0.153" in conf and "bind-interfaces" in conf
    assert "conf-dir=/etc/sshauto/filter/lists,*.conf" in conf
    assert "no-resolv" not in conf          # upstream stays the system default


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
