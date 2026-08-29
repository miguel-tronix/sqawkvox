"""SWE ingest: EPUB parsing, docs-site crawling, and extraction.

Docling has no EPUB support and fetches only single pages, so the SWE domain
prepares an :class:`~sqwakvox.domains.base.IngestPlan` in the shared docling
worker and reassembles multi-part sources itself.

* ``build_ingest_plan`` — classify the source (EPUB / docs site / PDF / URL /
  Markdown) and, for sites, run the crawler (see :mod:`...swe.crawl`).
* ``convert`` — run the actual conversion (EPUB chapter loop, site page loop,
  or the shared single-input Docling path).
* ``postprocess`` — build the payload the TUI/agent consume: TOC, code-block
  index, injection-scan flags, size accounting — and index the document for
  chunked retrieval (see :mod:`...swe.retrieval`).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from sqwakvox.domains.base import IngestPlan
from sqwakvox.models import StructuredDocument

logger = logging.getLogger(__name__)

#: Default max pages for PDFs — docs beyond this limit will have
#: ``needs_retrieval`` set so the agent uses the retrieval tool.
PDF_MAX_PAGES = 50

#: Above this size the raw markdown no longer fits a single agent context;
#: the agent gets TOC + first chunks and must use the retrieval tool.
MAX_CONTEXT_CHARS = 100_000

_MAX_TOC_ITEMS = 500
_MAX_CODE_BLOCKS = 200
_MAX_CODE_SNIPPET_CHARS = 2000

#: Polite delay between docs-site page conversions (in the worker).
SITE_PAGE_DELAY = 0.2


def build_ingest_plan(source: str, options: dict[str, Any] | None = None) -> IngestPlan:
    """Classify *source* into an ingest plan for the shared docling worker.

    ``options`` may carry ``crawl`` (True = docs-site crawl, False/omitted =
    single page) and ``max_pages``.  Crawling is strictly opt-in — never
    auto-triggered — so loading a URL is always a single-page fetch unless
    the user explicitly checks "Crawl docs site".
    """
    options = options or {}
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return IngestPlan(kind="epub", inputs=[source], metadata={"source_type": "epub"})
    if source.startswith(("http://", "https://")):
        if options.get("crawl") is True:
            from sqwakvox.domains.swe.crawl import DEFAULT_MAX_PAGES, crawl_site

            pages = crawl_site(
                source, max_pages=int(options.get("max_pages", DEFAULT_MAX_PAGES))
            )
            if len(pages) > 1:
                return IngestPlan(
                    kind="site",
                    inputs=pages,
                    metadata={"source_type": "site", "root_url": source},
                )
            # Crawl found a single page — fall through to a plain URL load.
        return IngestPlan(kind="single", inputs=[source], metadata={"source_type": "url"})
    if suffix == ".pdf":
        max_pages = int(options.get("max_pages", PDF_MAX_PAGES))
        return IngestPlan(
            kind="single",
            inputs=[source],
            metadata={
                "source_type": "pdf",
                "max_pages": max_pages,
            },
        )
    if suffix in (".md", ".txt", ".html", ".markdown"):
        return IngestPlan(
            kind="single", inputs=[source], metadata={"source_type": suffix.lstrip(".")}
        )
    return IngestPlan(kind="single", inputs=[source], metadata={"source_type": "file"})


def convert(
    controller: Any,
    source: str,
    plan: IngestPlan,
    is_cancelled: Any,
    page_range: tuple[int, int] | None = None,
) -> StructuredDocument | None:
    """Run the SWE conversion for *plan* inside the shared docling worker.

    ``page_range`` is forwarded to the PDF path so large documents load a
    slice at a time (the TUI then fetches further slices on demand).
    """
    if plan.kind == "epub":
        return _convert_epub(controller, source, is_cancelled)
    if plan.kind == "site":
        return _convert_site(controller, source, plan, is_cancelled)
    doc = controller.convert_document(
        source, is_cancelled, domain_id="swe", page_range=page_range
    )
    doc = doc if isinstance(doc, StructuredDocument) else None
    if doc is not None:
        doc.metadata.update(plan.metadata)
        doc.metadata["domain_id"] = "swe"
    return doc


def _convert_site(
    controller: Any,
    root_url: str,
    plan: IngestPlan,
    is_cancelled: Any,
) -> StructuredDocument | None:
    """Convert every crawled page (single-URL Docling fetch) and concatenate."""
    pages = plan.inputs
    parts: list[str] = []
    toc: list[str] = []
    converted = 0
    for url in pages:
        if is_cancelled():
            return None
        page_doc = controller.convert_document(url, is_cancelled, domain_id="swe")
        page_doc = page_doc if isinstance(page_doc, StructuredDocument) else None
        if page_doc is None or not (page_doc.raw_markdown or "").strip():
            continue
        title = _page_title(page_doc)
        toc.append(title)
        parts.append(f"## {title}\n\nURL: {url}\n\n{page_doc.raw_markdown}")
        converted += 1
        if converted < len(pages) and converted % 5 == 0:
            time.sleep(SITE_PAGE_DELAY)
    if not parts:
        raise ValueError(f"No pages could be fetched from docs site: {root_url}")
    host = urllib.parse.urlparse(root_url).netloc
    return StructuredDocument(
        file_name=f"{host} (docs)",
        raw_markdown="\n\n".join(parts),
        tables=[],
        metadata={
            "source_type": "site",
            "domain_id": "swe",
            "root_url": root_url,
            "toc": toc,
            "pages_converted": converted,
        },
    )


def _page_title(page_doc: StructuredDocument) -> str:
    for line in (page_doc.raw_markdown or "").splitlines():
        m = re.match(r"^#\s+(.*)", line)
        if m and m.group(1).strip():
            return m.group(1).strip()[:80]
    return page_doc.file_name or "Page"


def _convert_epub(
    controller: Any, source: str, is_cancelled: Any
) -> StructuredDocument | None:
    """Parse an EPUB and convert each chapter (XHTML) with Docling.

    Chapters are fed through ``controller.convert_html_string`` so the shared
    worker's Docling models do the heavy lifting; the resulting markdown is
    concatenated under ``## <chapter title>`` headings.
    """
    from ebooklib import ITEM_DOCUMENT, epub  # type: ignore[import-untyped]

    book = epub.read_epub(source)
    chapters: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        if is_cancelled():
            return None
        content = item.get_content().decode("utf-8", errors="replace")
        title = _chapter_title(content, item.get_name() or "")
        if title in seen:
            title = f"{title} ({len(chapters) + 1})"
        seen.add(title)
        md = controller.convert_html_string(
            content, name=item.get_name() or "", is_cancelled=is_cancelled
        )
        if not md.strip():
            continue
        chapters.append((title, md))

    if not chapters:
        raise ValueError(f"No readable chapters found in EPUB: {source}")

    toc = [title for title, _md in chapters]
    parts = [f"## {title}\n\n{md}" for title, md in chapters]
    return StructuredDocument(
        file_name=Path(source).name,
        raw_markdown="\n\n".join(parts),
        tables=[],
        metadata={
            "source_type": "epub",
            "domain_id": "swe",
            "toc": toc,
            "epub_chapters": len(chapters),
        },
    )


def postprocess(doc: StructuredDocument, source: str = "") -> dict[str, Any]:
    """Extract the SWE payload for a parsed document and index it."""
    del source
    md = doc.raw_markdown or ""
    payload: dict[str, Any] = {
        "toc": _extract_toc(md),
        "code_blocks": _extract_code_blocks(md),
        "injection_flags": scan_injections(md),
        "source_type": doc.metadata.get("source_type", "file"),
        "epub_chapters": doc.metadata.get("epub_chapters"),
        "pages_converted": doc.metadata.get("pages_converted"),
        "needs_retrieval": len(md) > MAX_CONTEXT_CHARS,
    }
    # Index for chunked retrieval so the search tool works for any SWE doc.
    payload["chunk_count"] = _index_for_retrieval(doc.file_name, md)
    return payload


def _index_for_retrieval(doc_id: str, md: str) -> int:
    from sqwakvox.domains.swe import retrieval

    try:
        return retrieval.index_document(doc_id, retrieval.chunk_markdown(md))
    except Exception as exc:  # indexing must never break parsing
        logger.warning("Failed to index %s for retrieval: %s", doc_id, exc)
        return 0


def scan_injections(md: str) -> list[str]:
    """Injection-scan ingested content; see :mod:`...swe.guardrails`."""
    from sqwakvox.domains.swe.guardrails import scan_document_injections

    return scan_document_injections(md)


def _chapter_title(content: str, name: str) -> str:
    """Best-effort chapter title: <title>, then first heading, then file name.

    ``ebooklib`` rewrites chapter XHTML and strips ``<title>``, so most real
    EPUBs fall back to the first ``<h1>``/``<h2>`` of the body.
    """
    for pattern in (
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
        r"<h2[^>]*>(.*?)</h2>",
    ):
        m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if title:
                return title
    stem = Path(name).stem.replace("_", " ").replace("-", " ").strip()
    return stem or "Chapter"


def _extract_toc(md: str) -> list[dict[str, object]]:
    toc: list[dict[str, object]] = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            toc.append({"level": len(m.group(1)), "title": m.group(2).strip()})
        if len(toc) >= _MAX_TOC_ITEMS:
            break
    return toc


def _extract_code_blocks(md: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    fence_re = re.compile(r"^```([A-Za-z0-9_+\-]*)\s*$")
    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n and len(blocks) < _MAX_CODE_BLOCKS:
        m = fence_re.match(lines[i])
        if m:
            language = m.group(1) or "text"
            start_line = i + 1
            buf: list[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            snippet = "\n".join(buf)
            blocks.append(
                {
                    "language": language,
                    "line": start_line,
                    "chars": len(snippet),
                    "snippet": snippet[:_MAX_CODE_SNIPPET_CHARS],
                }
            )
        i += 1
    return blocks
