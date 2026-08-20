import pytest
from textual.widgets import ListView, Tab, Tabs

from sqwakvox.app import SqwakvoxApp
from sqwakvox.models import StructuredDocument


@pytest.mark.asyncio
async def test_multi_document_tabs_creation_and_switching() -> None:
    app = SqwakvoxApp()
    async with app.run_test() as pilot:
        doc1 = StructuredDocument(file_name="doc1.pdf", raw_markdown="# Document 1 Content")
        doc2 = StructuredDocument(file_name="doc2.pdf", raw_markdown="# Document 2 Content")

        # Ingest first document
        app._on_parse_success(doc1, "doc1.pdf")
        await pilot.pause()

        tabs = app.query_one("#document-tabs", Tabs)
        tab_ids = [t.id for t in tabs.query(Tab)]
        assert tab_ids == ["tab_0"]
        assert tabs.active == "tab_0"
        assert app.active_document_name == "doc1.pdf"

        # Ingest second document — must not raise DuplicateIds error
        app._on_parse_success(doc2, "doc2.pdf")
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
