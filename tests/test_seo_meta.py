"""SEO / Google Search Console scaffolding — verification meta, robots, sitemap."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import _inject_site_verification, _render_index


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


# ── social preview (OpenGraph / Twitter) ──────────────────────────────


def test_render_index_makes_og_urls_absolute():
    html = (
        '<head><meta property="og:image" content="/static/og-image.png" />'
        '<meta name="twitter:image" content="/static/og-image.png" /></head>'
    )
    out = _render_index(html, base_url="https://app.example.com/")
    # Both relative image refs promoted to absolute; og:url injected.
    assert out.count('content="https://app.example.com/static/og-image.png"') == 2
    assert "/static/og-image.png" not in out.replace(
        "https://app.example.com/static/og-image.png", ""
    )
    assert '<meta property="og:url" content="https://app.example.com/" />' in out


def test_render_index_also_injects_verification_token():
    out = _render_index("<head></head>", base_url="https://x.test/", gsc_token="tok")
    assert 'name="google-site-verification" content="tok"' in out
    assert 'property="og:url" content="https://x.test/"' in out


def test_home_page_serves_absolute_social_tags(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'property="og:title"' in r.text
    assert 'name="twitter:card" content="summary_large_image"' in r.text
    # og:image absolute to the live host; og:url present.
    assert 'content="http://testserver/static/og-image.png"' in r.text
    assert 'property="og:url" content="http://testserver/"' in r.text


def test_og_image_is_served(client):
    r = client.get("/static/og-image.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


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
