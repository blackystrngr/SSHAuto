import socket
import threading

from core import blocklists as b

SAMPLE = """# comment
0.0.0.0 ads.example.com
127.0.0.1 localhost
127.0.0.1 tracker.example.net # trailing
plain.example.org
local=/dnsmasq.example.io/
address=/addr.example.io/0.0.0.0
! adblock comment
1.2.3.4
not a domain
UPPER.Example.COM
bad_tld.123
"""


def test_parse_formats():
    got = b.parse_domains(SAMPLE)
    assert got == {"ads.example.com", "tracker.example.net", "plain.example.org",
                   "dnsmasq.example.io", "addr.example.io", "upper.example.com"}


def test_parse_allowlist_covers_subdomains():
    got = b.parse_domains("a.example.com\nexample.com\nother.org\n", allow=["example.com"])
    assert got == {"other.org"}


def test_build_writes_local_lines_and_keeps_old_on_failure(tmp_path, monkeypatch):
    big = "".join(f"d{i}.example.com\n" for i in range(150))
    monkeypatch.setattr(b, "download", lambda url, retries=3: big)
    counts = b.build(["ads", "phishing"], lists_dir=tmp_path)
    assert counts == {"ads": 150, "phishing": 150}
    assert (tmp_path / "ads.conf").read_text().startswith("local=/d0.example.com/\n")
    assert not list(tmp_path.glob("*.tmp"))

    monkeypatch.setattr(b, "download", lambda url, retries=3: None)
    counts = b.build(["ads"], lists_dir=tmp_path)      # download fails -> previous list kept
    assert counts["ads"] == 150
    assert not (tmp_path / "phishing.conf").exists()   # disabled category removed


def test_tiny_or_garbage_list_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "download", lambda url, retries=3: "<html>captive portal</html>")
    assert b.build(["ads"], lists_dir=tmp_path) == {"ads": 0}
    assert not (tmp_path / "ads.conf").exists()


def _fake_dns(rcode):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))

    def run():
        data, addr = srv.recvfrom(512)
        srv.sendto(data[:2] + bytes([0x81, 0x80 | rcode]) + data[4:], addr)
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_dns_rcode_nxdomain_and_ok():
    assert b.dns_rcode("127.0.0.1", "blocked.example.com", port=_fake_dns(3)) == 3
    assert b.dns_rcode("127.0.0.1", "example.com", port=_fake_dns(0)) == 0


def test_dns_rcode_no_answer():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    assert b.dns_rcode("127.0.0.1", "example.com", port=port, timeout=0.3) is None
    s.close()


def test_collapse_drops_covered_subdomains():
    got = b.collapse({"x.com", "a.x.com", "b.a.x.com", "y.org", "ax.com"})
    assert got == {"x.com", "y.org", "ax.com"}
