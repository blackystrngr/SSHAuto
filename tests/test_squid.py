import pytest

from core.config import SQUID_PASS_DEFAULT, SQUID_USER_DEFAULT
from core.exceptions import ValidationError
from features.squid_proxy import _htpasswd_line, _validate


def test_defaults():
    assert (SQUID_USER_DEFAULT, SQUID_PASS_DEFAULT) == ("didntgo", "didntgo1#")


def test_htpasswd_sha_line():
    line = _htpasswd_line("didntgo", "didntgo1#")
    assert line == "didntgo:{SHA}lLwO447s0rtkET+KsaBRR6Z+a1M=\n"


@pytest.mark.parametrize("user,pw,port", [
    ("", "x", 3128), ("u", "", 3128), ("a:b", "x", 3128),
    ("u", "a b", 3128), ("u", "x", 0), ("u", "x", 70000),
])
def test_validate_rejects(user, pw, port):
    with pytest.raises(ValidationError):
        _validate(user, pw, port)


def test_validate_accepts_defaults():
    _validate(SQUID_USER_DEFAULT, SQUID_PASS_DEFAULT, 3128)
