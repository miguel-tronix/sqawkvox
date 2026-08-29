"""Tests for the SWE docs-site crawler."""

from __future__ import annotations

from sqwakvox.domains.swe import crawl


def test_looks_like_docs_root() -> None:
    assert crawl.looks_like_docs_root("https://docs.example.com")
    assert crawl.looks_like_docs_root("https://example.com/")
    assert crawl.looks_like_docs_root("https://example.com/docs/")
    assert crawl.looks_like_docs_root("https://example.com/guide")
    assert not crawl.looks_like_docs_root("https://example.com/guide/page.html")
    assert not crawl.looks_like_docs_root("https://example.com/pricing")


def test_extract_links_same_host_only() -> None:
    html = """
    <a href="/intro">Intro</a>
    <a href="https://docs.example.com/guide">Guide</a>
    <a href="https://other.example.com/x">External</a>
    <a href="mailto:x@y.z">Mail</a>
    <a href="#frag">Frag</a>
    <a href="https://docs.example.com/guide#anchor">Same with frag</a>
    """
    links = crawl.extract_links(html, "https://docs.example.com", "docs.example.com")
    # Fragment links resolve to their page (guide#anchor -> guide, already
    # seen); externals/mailto are dropped; the bare #frag resolves to the base.
    assert links == [
        "https://docs.example.com/intro",
        "https://docs.example.com/guide",
        "https://docs.example.com",
    ]


_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/</loc></url>
  <url><loc>https://docs.example.com/install</loc></url>
  <url><loc>https://docs.example.com/usage</loc></url>
  <url><loc>https://other.example.com/not-ours</loc></url>
</urlset>
"""

_ROOT_PAGE = """
<html><body>
  <a href="/install">Install</a>
  <a href="/usage">Usage</a>
  <a href="/install">duplicate</a>
  <a href="https://external.example.com/x">out</a>
</body></html>
"""

_INSTALL_PAGE = '<html><body><a href="/usage">Usage again</a><a href="/deep">Deep</a></body></html>'
_USAGE_PAGE = '<html><body><a href="/install">Back</a></body></html>'
_DEEP_PAGE = '<html><body>leaf</body></html>'


def _fetcher(pages: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in pages:
            raise RuntimeError(f"unexpected fetch: {url}")
        return pages[url]

    return fetch


def test_crawl_uses_sitemap_first() -> None:
    pages = {"https://docs.example.com/sitemap.xml": _SITEMAP}
    result = crawl.crawl_site("https://docs.example.com/", fetch=_fetcher(pages))
    # Same-host sitemap URLs only, deduplicated.
    assert result == [
        "https://docs.example.com/",
        "https://docs.example.com/install",
        "https://docs.example.com/usage",
    ]


def test_crawl_bfs_fallback_when_no_sitemap() -> None:
    pages = {
        "https://docs.example.com/": _ROOT_PAGE,
        "https://docs.example.com/install": _INSTALL_PAGE,
        "https://docs.example.com/usage": _USAGE_PAGE,
        "https://docs.example.com/deep": _DEEP_PAGE,
    }
    result = crawl.crawl_site("https://docs.example.com/", fetch=_fetcher(pages))
    assert result[0] == "https://docs.example.com/"
    assert "https://docs.example.com/install" in result
    assert "https://docs.example.com/usage" in result
    assert "https://external.example.com/x" not in result


def test_crawl_respects_max_pages() -> None:
    pages = {"https://docs.example.com/sitemap.xml": _SITEMAP}
    result = crawl.crawl_site("https://docs.example.com/", max_pages=2, fetch=_fetcher(pages))
    assert len(result) == 2


def test_crawl_single_page_without_sitemap_and_links() -> None:
    pages = {"https://docs.example.com/": "<html><body>no links</body></html>"}
    result = crawl.crawl_site("https://docs.example.com/", fetch=_fetcher(pages))
    assert result == ["https://docs.example.com/"]
