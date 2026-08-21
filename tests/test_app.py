import pytest
from textual.widgets import ListView, Tab, Tabs

from sqwakvox.app import SqwakvoxApp
from sqwakvox.models import StructuredDocument
from sqwakvox.presenter import TaskStatus


def _doc1() -> StructuredDocument:
    return StructuredDocument(file_name="doc1.pdf", raw_markdown="# Document 1 Content")


def _doc2() -> StructuredDocument:
    return StructuredDocument(file_name="doc2.pdf", raw_markdown="# Document 2 Content")


@pytest.mark.asyncio
async def test_multi_document_tabs_creation_and_switching() -> None:
    app = SqwakvoxApp()
    async with app.run_test() as pilot:
        doc_a = _doc1()
        doc_b = _doc2()

        # Ingest first document
        app._on_parse_success(doc_a, "doc1.pdf")
        await pilot.pause()

        tabs = app.query_one("#document-tabs", Tabs)
        tab_ids = [t.id for t in tabs.query(Tab)]
        assert tab_ids == ["tab_0"]
        assert tabs.active == "tab_0"
        assert app.active_document_name == "doc1.pdf"

        # Ingest second document — must not raise DuplicateIds error
        app._on_parse_success(doc_b, "doc2.pdf")
        await pilot.pause()

        tab_ids = [t.id for t in tabs.query(Tab)]
        assert tab_ids == ["tab_0", "tab_1"]
        assert tabs.active == "tab_1"
        assert app.active_document_name == "doc2.pdf"

        # Switch back to first document by activating tab_0
        tabs.active = "tab_0"
        await pilot.pause()

        assert app.active_document_name == "doc1.pdf"
        assert app.doc_context == "# Document 1 Content"

        # Switch to second document via Ingest History ListView
        history_list = app.query_one("#ingest-history", ListView)
        history_list.index = 1
        history_list.action_select_cursor()
        await pilot.pause()

        assert app.active_document_name == "doc2.pdf"
        assert tabs.active == "tab_1"


def _stub_parse(
    monkeypatch: pytest.MonkeyPatch, app: SqwakvoxApp, doc: StructuredDocument
) -> list[tuple[str, str | None]]:
    """Replace presenter.parse_document with a fake that completes instantly."""
    routed: list[tuple[str, str | None]] = []

    class FakeHandle:
        status = TaskStatus.SUCCESS
        result = None
        error = None

        async def wait(self, _timeout: float | None = None) -> TaskStatus:
            return TaskStatus.SUCCESS

    async def fake_parse(source: str, **kwargs):
        routed.append((source, kwargs.get("queue")))
        if kwargs.get("on_complete") is not None:
            kwargs["on_complete"](TaskStatus.SUCCESS, doc)
        return FakeHandle()

    monkeypatch.setattr(app.presenter, "parse_document", fake_parse)
    return routed


@pytest.mark.asyncio
async def test_document_load_spawns_dedicated_worker_per_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading a document must spawn a worker on its own per-tab queue and
    route the parse task there; each subsequent tab gets its own worker."""
    app = SqwakvoxApp()
    async with app.run_test():
        ensured: list[str] = []
        monkeypatch.setattr(
            app.worker_manager,
            "ensure_worker",
            lambda queue: ensured.append(queue) or True,
        )

        routed1 = _stub_parse(monkeypatch, app, _doc1())
        await app._dispatch_parse("/tmp/doc1.pdf")

        assert ensured == ["sqwakvox.doc0"]
        assert routed1 == [("/tmp/doc1.pdf", "sqwakvox.doc0")]
        assert app.active_document_name == "doc1.pdf"

        routed2 = _stub_parse(monkeypatch, app, _doc2())
        await app._dispatch_parse("/tmp/doc2.pdf")

        assert ensured == ["sqwakvox.doc0", "sqwakvox.doc1"]
        assert routed2 == [("/tmp/doc2.pdf", "sqwakvox.doc1")]
        assert app.active_document_name == "doc2.pdf"


@pytest.mark.asyncio
async def test_active_queue_follows_selected_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat/cross-validate tasks route to the active document's worker."""
    app = SqwakvoxApp()
    async with app.run_test() as pilot:
        ensured: list[str] = []
        monkeypatch.setattr(
            app.worker_manager,
            "ensure_worker",
            lambda queue: ensured.append(queue) or True,
        )

        _stub_parse_routed1 = _stub_parse(monkeypatch, app, _doc1())
        await app._dispatch_parse("/tmp/doc1.pdf")
        _stub_parse_routed2 = _stub_parse(monkeypatch, app, _doc2())
        await app._dispatch_parse("/tmp/doc2.pdf")

        assert app._active_queue() == "sqwakvox.doc1"  # doc2 loaded last

        tabs = app.query_one("#document-tabs", Tabs)
        tabs.active = "tab_0"
        await pilot.pause()

        assert app.active_document_name == "doc1.pdf"
        assert app._active_queue() == "sqwakvox.doc0"


@pytest.mark.asyncio
async def test_managed_workers_disabled_falls_back_to_default_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQWAKVOX_MANAGED_WORKERS=0 restores bring-your-own-worker behaviour."""
    monkeypatch.setenv("SQWAKVOX_MANAGED_WORKERS", "0")
    app = SqwakvoxApp()
    async with app.run_test():
        ensured: list[str] = []
        monkeypatch.setattr(
            app.worker_manager,
            "ensure_worker",
            lambda queue: ensured.append(queue) or True,
        )

        routed = _stub_parse(monkeypatch, app, _doc1())
        await app._dispatch_parse("/tmp/doc1.pdf")

        assert ensured == []  # no worker spawned
        assert routed == [("/tmp/doc1.pdf", None)]  # default Celery queue
