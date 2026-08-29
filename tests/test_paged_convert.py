"""Tests for incremental (page-slice) PDF conversion.

Large PDFs (e.g. numpyexer.pdf, 162 pages) time out when converted in one
shot, so :meth:`~sqwakvox.controller.AppController.convert_document` accepts a
``page_range`` and stamps paging metadata on the result.  These tests cover
the backend wiring without invoking Docling's OCR models.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqwakvox.controller import AppController
from sqwakvox.presenter import Presenter


class _FakePage:
    pass


class _FakeResult:
    def __init__(self, md: str, n_pages: int) -> None:
        self.document = MagicMock()
        self.document.export_to_markdown.return_value = md
        self.pages = [_FakePage() for _ in range(n_pages)]


class _FakeConverter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def convert(self, source, **kwargs):
        self.calls.append((source, kwargs))
        page_range = kwargs.get("page_range")
        if page_range is not None:
            start, end = page_range
            n = end - start + 1
            md = f"# slice {start}-{end}\n"
        else:
            n = 162
            md = "# whole doc\n"
        return _FakeResult(md, n)


def test_convert_document_passes_page_range() -> None:
    """A page_range is forwarded to Docling and recorded in metadata."""
    controller = AppController()
    fake = _FakeConverter()
    controller.converter = fake

    doc = controller.convert_document(
        "doc.pdf", lambda: False, domain_id="swe", page_range=(1, 10)
    )
    assert doc is not None
    assert fake.calls[0][1].get("page_range") == (1, 10)
    assert doc.metadata["page_range"] == [1, 10]
    assert doc.metadata["pages_in_batch"] == 10
    # No real file -> pdf_page_count returns None; slice size still accurate.
    assert doc.metadata["total_pages"] is None


def test_convert_document_whole_doc_has_no_page_range() -> None:
    """Without a page_range the legacy whole-document path is unchanged."""
    controller = AppController()
    fake = _FakeConverter()
    controller.converter = fake

    doc = controller.convert_document("doc.pdf", lambda: False, domain_id="financial")
    assert doc is not None
    assert doc.metadata["page_range"] is None
    assert "page_range" not in fake.calls[0][1]


def test_presenter_parse_document_forwards_page_range() -> None:
    """parse_document forwards page_range into the Celery task kwargs."""

    captured: dict[str, object] = {}

    async def fake_submit(_task_name, args=None, kwargs=None, **_extra):
        captured["args"] = args
        captured["kwargs"] = kwargs
        handle = MagicMock()
        handle.wait = MagicMock()
        return handle

    original = Presenter.submit_task
    Presenter.submit_task = staticmethod(fake_submit)  # type: ignore[assignment]
    try:
        presenter = Presenter()
        import asyncio

        asyncio.run(
            presenter.parse_document("doc.pdf", domain_id="swe", page_range=(11, 20))
        )
    finally:
        Presenter.submit_task = original

    assert captured["args"] == ["doc.pdf"]
    assert captured["kwargs"].get("page_range") == [11, 20]
    assert captured["kwargs"].get("domain_id") == "swe"
