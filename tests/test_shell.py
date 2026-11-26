import pytest

from core.exceptions import ShellError
from core.shell import Shell


def test_ok():
    assert Shell.run("echo hi").stdout.strip() == "hi"


def test_failure_raises_with_check():
    with pytest.raises(ShellError):
        Shell.run("false")


def test_failure_returns_without_check():
    assert not Shell.run("false", check=False).ok


def test_timeout_does_not_raise_without_check():
    r = Shell.run("sleep 3", check=False, timeout=1)
    assert r.returncode == -1


def test_timeout_raises_with_check():
    with pytest.raises(ShellError):
        Shell.run("sleep 3", timeout=1)
