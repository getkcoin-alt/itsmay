"""Web research connector — server-side search and URL fetch.

Tools:
  web.search  — DuckDuckGo search, returns top N result summaries
  web.fetch   — fetch a URL and return extracted plain text

No API key required. Uses only httpx (already a project dependency)
and Python stdlib html.parser.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.logging import get_logger
from core.util.ssrf import SSRFError, guarded_get

log = get_logger(__name__)

_TOOLS = [
    ToolSpec(
        name="search",
        description=(
            "Search the web via DuckDuckGo. Returns the top `n` results as "
            "title, URL, and snippet. Use for current information, documentation, "
            "changelogs, pricing, news, or anything not in long-term memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of results to return (1–10, default 5).",
                },
            },
            "required": ["query"],
        },
        executor="server",
    ),
    ToolSpec(
        name="fetch",
        description=(
            "Fetch a URL and return the plain-text content (tags stripped). "
            "Use after web.search when you need the full content of a specific page. "
            "Capped at 3000 chars to fit in context."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch.",
                },
            },
            "required": ["url"],
        },
        executor="server",
    ),
]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_FETCH_CHARS = 3000
_MAX_RESULT_CHARS = 3000


class _StripTagsParser(HTMLParser):
    """Minimal HTML → plain text: drops scripts/styles, collapses whitespace."""

    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag.lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = " ".join(self._parts)
        # Collapse whitespace runs.
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _strip_html(html: str) -> str:
    p = _StripTagsParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.get_text()


def _ddg_real_url(href: str) -> str:
    """Convert DuckDuckGo redirect URLs to the actual destination URL."""
    if not href:
        return href
    if href.startswith("/l/") or "//duckduckgo.com/l/" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            return qs.get("uddg", [href])[0]
        except Exception:
            pass
    return href


def _parse_ddg_html(html: str, n: int) -> list[str]:
    """Extract result blocks from DDG HTML-mode response."""
    # Match a result__a anchor (title + href) followed by a result__snippet anchor.
    block_re = re.compile(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        r".*?"
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
        re.DOTALL | re.IGNORECASE,
    )
    results: list[str] = []
    for m in block_re.finditer(html):
        href, title_html, snippet_html = m.groups()
        title = _strip_html(title_html).strip()
        snippet = _strip_html(snippet_html).strip()
        url = _ddg_real_url(href)
        if title:
            results.append(f"{title}\n{url}\n{snippet}")
        if len(results) >= n:
            break
    return results


async def _ddg_search(query: str, n: int) -> str:
    params = urlencode({"q": query, "b": "", "kl": "us-en"})
    url = f"https://html.duckduckgo.com/html/?{params}"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA},
            follow_redirects=True,
            timeout=15,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        log.warning("web.search_failed", err=str(e))
        return f"Search failed: {e}"

    results = _parse_ddg_html(resp.text, n)
    if not results:
        return f"No results found for: {query}"
    text = "\n\n".join(results)
    return text[:_MAX_RESULT_CHARS]


async def _fetch_url(url: str) -> str:
    try:
        # follow_redirects=False: guarded_get follows them by hand and re-checks
        # every hop for SSRF, so a public→internal redirect can't slip through.
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA},
            follow_redirects=False,
            timeout=15,
        ) as client:
            resp = await guarded_get(url, client=client)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "html" in ct or "text" in ct:
                text = _strip_html(resp.text)
            else:
                text = resp.text
            return text[:_MAX_FETCH_CHARS]
    except SSRFError as e:
        log.warning("web.fetch_blocked", url=url, reason=str(e))
        return f"Refused: {e} (I only fetch public web addresses)."
    except httpx.HTTPStatusError as e:
        return f"HTTP {e.response.status_code} fetching {url}"
    except Exception as e:
        return f"Fetch failed: {e}"


class WebConnector(Connector):
    manifest = ConnectorManifest(
        name="web",
        version="0.1.0",
        description="Search the web (DuckDuckGo) and fetch URL content.",
        tools=_TOOLS,
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "search":
            query = str(args.get("query", "")).strip()
            n = max(1, min(10, int(args.get("n") or 5)))
            log.info("web.search", query=query, n=n)
            return await _ddg_search(query, n)

        if action == "fetch":
            url = str(args.get("url", "")).strip()
            log.info("web.fetch", url=url)
            return await _fetch_url(url)

        return f"unknown web action: {action}"
