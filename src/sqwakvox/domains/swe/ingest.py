"""SWE ingest: EPUB parsing, single-page URL/PDF handling, and extraction.

Docling has no EPUB support and fetches only single pages, so the SWE domain
prepares an :class:`~sqwakvox.domains.base.IngestPlan` in the shared docling
worker and reassembles multi-chapter sources itself.

* ``build_ingest_plan`` — classify the source (EPUB / PDF / Markdown / URL).
* ``convert`` — run the actual conversion (EPUB chapter loop, or the shared
  single-input Docling path).
* ``postprocess`` — build the payload the TUI/agent consume: TOC, code-block
  index, injection-scan flags, size accounting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqwakvox.domains.base import IngestPlan
from sqwakvox.models import StructuredDocument

#: Above this size the raw markdown no longer fits a single agent context;
#: the TUI warns and (Phase 2) chunked retrieval kicks in.
MAX_CONTEXT_CHARS = 100_000

_MAX_TOC_ITEMS = 500
_MAX_CODE_BLOCKS = 200
_MAX_CODE_SNIPPET_CHARS = 2000


def build_ingest_plan(source: str, options: dict[str, Any] | None = None) -> IngestPlan:
    """Classify *source* into an ingest plan for the shared docling worker."""
    del options
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return IngestPlan(kind="epub", inputs=[source], metadata={"source_type": "epub"})
    if source.startswith(("http://", "https://")):
        return IngestPlan(kind="single", inputs=[source], metadata={"source_type": "url"})
    if suffix in (".pdf", ".md", ".txt", ".html", ".markdown"):
        return IngestPlan(
        kind="single", inputs=[source], metadata={"source_type": suffix.lstrip(".")}
    )
    return IngestPlan(kind="single", inputs=[source], metadata={"source_type": "file"})


def convert(
    controller: Any,
    source: str,
    plan: IngestPlan,
    is_cancelled: Any,
) -> StructuredDocument | None:
    """Run the SWE conversion for *plan* inside the shared docling worker."""
    if plan.kind == "epub":
        return _convert_epub(controller, source, is_cancelled)
    doc = controller.convert_document(source, is_cancelled, domain_id="swe")
    doc = doc if isinstance(doc, StructuredDocument) else None
    if doc is not None:
        doc.metadata.update(plan.metadata)
        doc.metadata["domain_id"] = "swe"
    return doc


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
    """Extract the SWE payload for a parsed document."""
    del source
    md = doc.raw_markdown or ""
    payload: dict[str, Any] = {
        "toc": _extract_toc(md),
        "code_blocks": _extract_code_blocks(md),
        "injection_flags": scan_injections(md),
        "source_type": doc.metadata.get("source_type", "file"),
        "epub_chapters": doc.metadata.get("epub_chapters"),
        "needs_retrieval": len(md) > MAX_CONTEXT_CHARS,
    }
    return payload


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
