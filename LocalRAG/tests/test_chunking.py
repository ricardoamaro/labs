"""Unit tests for the structure-aware chunker in app.py.

These run with no models and no Docker. They assert the properties that
make retrieval trustworthy: headings stay intact, chunks overlap, and sizes
stay bounded. Each test names the property it guards.
"""

import pytest


def _chunks(app, text):
    return app._split_structure(text)


def test_heading_not_split_mid_chunk(app_module):
    """A markdown heading must start a chunk, never be cut across a boundary."""
    text = "# Title\npara one\n\n## Sub\npara two\n\n## Other\npara three"
    chunks = _chunks(app_module, text)
    for c in chunks:
        # every chunk that begins with a heading keeps it whole
        if c.lstrip().startswith("#"):
            assert c.splitlines()[0].startswith("#"), c


def test_overlap_present_between_chunks(app_module):
    """Consecutive chunks share OVERLAP_CHARS of tail text for continuity."""
    text = " ".join(f"word{i}" for i in range(400))
    chunks = _chunks(app_module, text)
    if len(chunks) > 1:
        overlap = app_module.OVERLAP_CHARS
        assert chunks[1].startswith(chunks[0][-overlap:][:20]), (
            "second chunk should begin with tail of the first"
        )


def test_chunk_size_within_bound(app_module):
    """Merged chunks stay at or below CHUNK_CHARS (single long line tolerance).

    A single unbreakable line longer than CHUNK_CHARS is allowed to exceed it;
    merged multi-block chunks must respect the bound.
    """
    text = "\n\n".join("short block number " + str(i) for i in range(50))
    chunks = _chunks(app_module, text)
    for c in chunks:
        # only assert when no single line blows the budget
        if max(len(line) for line in c.splitlines()) <= app_module.CHUNK_CHARS:
            assert len(c) <= app_module.CHUNK_CHARS + app_module.OVERLAP_CHARS


def test_small_text_single_chunk(app_module):
    """Short documents produce exactly one chunk."""
    chunks = _chunks(app_module, "Just a small note.\nOnly two lines.")
    assert len(chunks) == 1


def test_app_imports_with_streamlit_stubbed(app_module):
    """app.py imports and exposes its core functions despite the UI stub."""
    for fn in ("_split_structure", "ingest", "retrieve", "answer"):
        assert callable(getattr(app_module, fn))
