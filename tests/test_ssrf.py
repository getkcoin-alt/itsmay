"""#18 — SSRF guard for web.fetch. Blocks internal targets on every hop.

Pure validation is resolver-injectable, so DNS-based SSRF is covered offline; the
connector integration uses IP-literal targets that are refused BEFORE any network.
"""

from __future__ import annotations

import pytest

from core.connectors.web.connector import _fetch_url
from core.util.ssrf import SSRFError, guarded_get, validate_url


def _pub(_host):
    return ["8.8.8.8"]


# ── validate_url: blocked targets ─────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1:8000/v1/chat",             # the operator API (loopback)
        "http://localhost/",                          # loopback by name (resolves)
        "http://10.0.0.5/",                           # private
        "http://192.168.1.1/",                        # private
        "http://172.16.0.1/",                         # private
        "http://0.0.0.0/",                            # unspecified
        "http://[::1]/",                              # IPv6 loopback
        "http://[fe80::1]/",                          # IPv6 link-local
        "http://[::ffff:127.0.0.1]/",                 # IPv4-mapped loopback
    ],
)
def test_validate_url_blocks_internal(url):
    # localhost needs resolution; the IP literals don't. A resolver that returns a
    # loopback for "localhost" mirrors the real box.
    resolver = (lambda _h: ["127.0.0.1"]) if "localhost" in url else _pub
    with pytest.raises(SSRFError):
        validate_url(url, resolver=resolver)


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://x/"])
def test_validate_url_blocks_bad_scheme(url):
    with pytest.raises(SSRFError):
        validate_url(url, resolver=_pub)


def test_validate_url_requires_host():
    with pytest.raises(SSRFError):
        validate_url("http:///just-a-path", resolver=_pub)


# ── validate_url: allowed + DNS-based SSRF ────────────────────────────


def test_validate_url_allows_public():
    assert validate_url("https://8.8.8.8/", resolver=_pub) == "8.8.8.8"
    assert validate_url("https://example.com/page", resolver=_pub) == "example.com"


def test_validate_url_blocks_dns_rebinding():
    # A public-looking hostname that resolves to loopback is refused.
    with pytest.raises(SSRFError):
        validate_url("http://evil.example/", resolver=lambda _h: ["127.0.0.1"])


def test_validate_url_blocks_if_any_resolved_ip_is_private():
    with pytest.raises(SSRFError):
        validate_url("http://mixed.example/", resolver=lambda _h: ["8.8.8.8", "10.0.0.1"])


def test_validate_url_resolution_failure_is_blocked():
    def boom(_h):
        raise OSError("nxdomain")

    with pytest.raises(SSRFError):
        validate_url("http://nope.example/", resolver=boom)


# ── guarded_get: per-hop redirect validation ──────────────────────────


class FakeResp:
    def __init__(self, status, headers=None, text=""):
        self.status_code = status
        self.headers = headers or {}
        self.text = text


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.gets: list[str] = []

    async def get(self, url):
        self.gets.append(url)
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


async def test_guarded_get_returns_public_response():
    client = FakeClient([FakeResp(200, text="hi")])
    resp = await guarded_get("http://public.example/", client=client, resolver=_pub)
    assert resp.status_code == 200
    assert client.gets == ["http://public.example/"]


async def test_guarded_get_blocks_public_to_internal_redirect():
    client = FakeClient(
        [FakeResp(302, {"location": "http://169.254.169.254/latest/"}), FakeResp(200)]
    )
    with pytest.raises(SSRFError):
        await guarded_get("http://public.example/", client=client, resolver=_pub)
    assert client.gets == ["http://public.example/"]  # internal hop never fetched


async def test_guarded_get_follows_safe_relative_redirect():
    client = FakeClient([FakeResp(302, {"location": "/next"}), FakeResp(200, text="ok")])
    resp = await guarded_get("http://public.example/page", client=client, resolver=_pub)
    assert resp.status_code == 200
    assert client.gets == ["http://public.example/page", "http://public.example/next"]


async def test_guarded_get_rejects_redirect_loop():
    client = FakeClient([FakeResp(302, {"location": "http://public.example/loop"})])
    with pytest.raises(SSRFError):
        await guarded_get("http://public.example/", client=client, resolver=_pub, max_redirects=3)


# ── connector integration (blocked before any network) ────────────────


async def test_web_fetch_refuses_metadata_without_network():
    out = await _fetch_url("http://169.254.169.254/latest/meta-data/")
    assert out.startswith("Refused:")


async def test_web_fetch_refuses_localhost_api():
    out = await _fetch_url("http://127.0.0.1:8000/v1/chat")
    assert out.startswith("Refused:")


async def test_web_fetch_refuses_bad_scheme():
    out = await _fetch_url("file:///etc/passwd")
    assert out.startswith("Refused:")
