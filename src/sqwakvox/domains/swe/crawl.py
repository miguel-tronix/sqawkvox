"""Docs-site crawler for the SWE domain.

Docling fetches single pages only; a documentation *site* (multi-page) needs
a small crawler that collects same-host page URLs (sitemap-first, BFS
fallback), which the shared docling worker then converts page by page.

The crawler is conservative by design: same-host only, depth and page caps,
and a polite delay between page fetches.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 25
DEFAULT_MAX_DEPTH = 2
POLITE_DELAY = 0.2

_USER_AGENT = "sqwakvox-swe-crawler/1.0 (+local document assistant)"
_TIMEOUT_SECONDS = 15.0

FetchFn = Callable[[str], str]

#: Path segments that hint "docs site root" for auto-detection.
_DOCS_ROOT_SEGMENTS = {"docs", "documentation", "guide", "guides", "learn", "tutorial", "reference"}


def looks_like_docs_root(url: str) -> bool:
    """Heuristic: is *url* likely a multi-page docs site root?"""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    if path in ("", "/"):
        return True
    last = path.rsplit("/", 1)[-1].lower()
    return last in _DOCS_ROOT_SEGMENTS


def _fetch(url: str) -> str:
    """Fetch *url* and return its text (default fetcher)."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


def _normalize(link: str, base_url: str) -> str | None:
    """Resolve *link* against *base_url*; None for non-http(s)/external links."""
    try:
        joined = urllib.parse.urljoin(base_url, link.strip())
    except ValueError:
        return None
    parsed = urllib.parse.urlparse(joined)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Drop fragments; keep query strings (docs sites often use them).
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _same_host(url: str, host: str) -> bool:
    return urllib.parse.urlparse(url).netloc == host


def extract_links(html: str, base_url: str, host: str) -> list[str]:
    """Same-host http(s) links from *html*, de-duplicated, in document order."""
    seen: set[str] = set()
    links: list[str] = []
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", html, re.IGNORECASE):
        normalized = _normalize(href, base_url)
        if normalized is None or normalized in seen:
            continue
        if not _same_host(normalized, host):
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def _parse_sitemap(xml: str) -> list[str]:
    """Extract page URLs from a sitemap XML body (same-host filtering later)."""
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.IGNORECASE | re.DOTALL)


def crawl_site(
    root_url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    fetch: FetchFn | None = None,
) -> list[str]:
    """Return same-host page URLs to convert, starting from *root_url*.

    Sitemap-first: if ``<root>/sitemap.xml`` exists and yields pages, use it
    (capped at *max_pages*).  Otherwise BFS from the root page, following
    same-host links up to *max_depth*, capped at *max_pages*.
    """
    fetch = fetch or _fetch
    host = urllib.parse.urlparse(root_url).netloc
    if not host:
        logger.warning("Crawl target has no host: %s", root_url)
        return [root_url]

    # --- Sitemap first ---
    sitemap_url = _normalize("/sitemap.xml", root_url)
    if sitemap_url is not None:
        try:
            xml = fetch(sitemap_url)
        except Exception:
            pass
        else:
            sitemap_pages = [u for u in _parse_sitemap(xml) if _same_host(u, host)]
            sitemap_pages = list(dict.fromkeys(sitemap_pages))[:max_pages]
            if sitemap_pages:
                logger.info(
                    "Crawling %d pages from sitemap %s", len(sitemap_pages), sitemap_url
                )
                return sitemap_pages

    # --- BFS fallback ---
    logger.info("No sitemap at %s; crawling links from the root page", root_url)
    queue: deque[tuple[str, int]] = deque([(root_url, 0)])
    visited: set[str] = set()
    found: list[str] = []
    while queue and len(found) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            html = fetch(url)
        except Exception as exc:
            logger.warning("Crawl fetch failed for %s: %s", url, exc)
            continue
        if len(found) >= max_pages:
            break
        found.append(url)
        if depth >= max_depth or len(found) >= max_pages:
            continue
        for link in extract_links(html, url, host):
            if link not in visited and link not in {u for u, _d in queue}:
                queue.append((link, depth + 1))
        if len(queue) > 1 and len(found) % 5 == 0:
            time.sleep(POLITE_DELAY)
    logger.info("Crawl collected %d pages from %s", len(found), root_url)
    return found
