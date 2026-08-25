"""Tests for SWE chunked retrieval: chunker, FTS5 index, and context assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from sqwakvox.domains.swe import retrieval
from sqwakvox.models import StructuredDocument


@pytest.fixture
def index_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SQWAKVOX_SWE_INDEX_DIR", str(tmp_path))


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #


def test_chunk_markdown_splits_on_headings() -> None:
    md = "# Title\n\nIntro.\n\n## Section A\n\nText A.\n\n## Section B\n\nText B.\n"
    chunks = retrieval.chunk_markdown(md, max_chars=3000)
    assert len(chunks) == 1
    assert "# Title" in chunks[0]
    assert "## Section A" in chunks[0]


def test_chunk_markdown_splits_oversized_sections_with_overlap() -> None:
    md = "# Big\n\n" + "word " * 2000  # ~10k chars, no sub-headings
    chunks = retrieval.chunk_markdown(md, max_chars=1000, overlap=100)
    assert len(chunks) > 3
    # Every chunk is within the cap and content is not lost.
    assert all(len(c) <= 1000 for c in chunks)
    assert "word " * 50 in chunks[0]


def test_chunk_markdown_merges_small_sections() -> None:
    md = "\n".join(f"## H{i}\n\nbody{i}\n" for i in range(6))
    chunks = retrieval.chunk_markdown(md, max_chars=2000)
    assert len(chunks) < 6  # small sections merged
    assert all("## H" in c for c in chunks)


def test_chunk_markdown_empty() -> None:
    assert retrieval.chunk_markdown("") == []


# --------------------------------------------------------------------------- #
# Index + search
# --------------------------------------------------------------------------- #


def test_index_and_search_roundtrip(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    md = (
        "# Refactoring\n\n## Chapter 1: Testing\n\nAlways write fast tests.\n\n"
        "## Chapter 2: Patterns\n\nUse the strategy pattern for variations.\n"
    )
    chunks = retrieval.chunk_markdown(md)
    assert retrieval.index_document("Refactoring.epub", chunks) == len(chunks)
    assert retrieval.count_chunks("Refactoring.epub") == len(chunks)

    results = retrieval.search_document("Refactoring.epub", "strategy pattern", k=5)
    assert results
    assert any("strategy" in r["content"] for r in results)

    # Query with no match returns nothing.
    assert retrieval.search_document("Refactoring.epub", "zzzzz", k=5) == []


def test_search_scoped_to_document(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    retrieval.index_document("doc-a", ["alpha beta content"])
    retrieval.index_document("doc-b", ["gamma delta content"])
    results = retrieval.search_document("doc-a", "gamma", k=5)
    assert results == []  # gamma lives in doc-b only


def test_reindex_replaces_chunks(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    retrieval.index_document("doc", ["old content"])
    retrieval.index_document("doc", ["new content"])
    assert retrieval.count_chunks("doc") == 1
    assert retrieval.search_document("doc", "old", k=5) == []


def test_delete_document(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    retrieval.index_document("doc", ["content here"])
    retrieval.delete_document("doc")
    assert retrieval.count_chunks("doc") == 0
    assert retrieval.list_documents() == []


def test_list_documents(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    retrieval.index_document("a", ["x", "y"])
    retrieval.index_document("b", ["z"])
    docs = dict(retrieval.list_documents())
    assert docs == {"a": 2, "b": 1}


# --------------------------------------------------------------------------- #
# Context assembly
# --------------------------------------------------------------------------- #


def test_build_retrieval_context_small_doc_returns_full_markdown() -> None:
    doc = StructuredDocument(file_name="small.md", raw_markdown="# Hi\n\ncontent")
    assert retrieval.build_retrieval_context(doc) == "# Hi\n\ncontent"


def test_build_retrieval_context_large_doc_instructs_search(index_env: None) -> None:
    del index_env  # fixture: points SQWAKVOX_SWE_INDEX_DIR at tmp_path
    from sqwakvox.domains.swe.ingest import MAX_CONTEXT_CHARS

    md = "# Big Book\n\n## Section A\n\n" + "x" * 5000 + "\n\n## Section B\n\n" + "y" * 5000
    doc = StructuredDocument(
        file_name="big.epub",
        raw_markdown=md,
        metadata={"needs_retrieval": True, "toc": [{"level": 1, "title": "Big Book"}]},
    )
    ctx = retrieval.build_retrieval_context(doc)
    assert len(ctx) < MAX_CONTEXT_CHARS
    assert "big.epub" in ctx
    assert "search_document" in ctx
    assert "Table of Contents" in ctx
    assert "## Section A" in ctx
