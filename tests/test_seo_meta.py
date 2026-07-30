"""SEO / Google Search Console scaffolding — verification meta, robots, sitemap."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import _inject_site_verification


@pytest.fixture
def client():
    from apps.api.main import app

    return TestClient(app)


# ── verification meta injection (pure) ────────────────────────────────


def test_inject_meta_noop_without_token():
    html = "<head><title>x</title></head><body></body>"
    assert _inject_site_verification(html, "") == html
    assert _inject_site_verification(html, "   ") == html


def test_inject_meta_inserts_before_head_close():
    html = "<head><title>x</title></head><body></body>"
    out = _inject_site_verification(html, "abc123")
    assert '<meta name="google-site-verification" content="abc123" />' in out
    assert out.index("google-site-verification") < out.index("</head>")


def test_inject_meta_sanitizes_token():
    out = _inject_site_verification("<head></head>", 'a"b<c>d')
    assert 'content="a&quot;bcd"' in out  # quotes escaped, angle brackets stripped


def test_inject_meta_without_head_prepends():
    out = _inject_site_verification("<body>hi</body>", "tok")
    assert out.startswith('  <meta name="google-site-verification" content="tok" />')


# ── robots + sitemap (served open, for crawlers) ──────────────────────


def test_robots_txt_open_and_points_to_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200  # reachable without a bearer token
    assert "text/plain" in r.headers["content-type"]
    assert "Disallow: /v1/" in r.text  # API surface kept out of the index
    assert "Sitemap: http://testserver/sitemap.xml" in r.text


def test_sitemap_xml_lists_root(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    assert "<urlset" in r.text
    assert "<loc>http://testserver/</loc>" in r.text
