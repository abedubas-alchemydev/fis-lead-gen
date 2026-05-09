"""Unit tests for the recursive chunker."""

from __future__ import annotations

from app.services.vault_chunker import chunk_text


def test_empty_input_returns_empty_list() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []
    assert chunk_text("\n\n\n") == []


def test_short_text_yields_single_chunk() -> None:
    text = "A short paragraph that fits comfortably under the budget."
    out = chunk_text(text, max_tokens=500, overlap_tokens=0)
    assert out == [text]


def test_paragraph_split_when_under_budget_per_paragraph() -> None:
    text = "para one.\n\npara two.\n\npara three."
    out = chunk_text(text, max_tokens=2, overlap_tokens=0)
    assert len(out) == 3
    assert all(p in text for p in out)


def test_sentence_repack_keeps_chunks_under_budget() -> None:
    sentences = ". ".join(f"sentence number {i}" for i in range(20)) + "."
    out = chunk_text(sentences, max_tokens=8, overlap_tokens=0)
    assert all(len(c.split()) <= 12 for c in out), [len(c.split()) for c in out]
    assert len(out) >= 4  # too many sentences for one chunk


def test_word_fallback_for_run_on_text() -> None:
    text = " ".join(f"word{i}" for i in range(200))
    out = chunk_text(text, max_tokens=50, overlap_tokens=0)
    assert all(len(c.split()) <= 50 for c in out), [len(c.split()) for c in out]
    assert len(out) >= 4


def test_overlap_prepends_tail_of_previous_chunk() -> None:
    text = ". ".join(f"sentence {i}" for i in range(20)) + "."
    no_overlap = chunk_text(text, max_tokens=8, overlap_tokens=0)
    with_overlap = chunk_text(text, max_tokens=8, overlap_tokens=4)
    # Overlap variant produces same number of chunks but each (after the
    # first) is longer than its no-overlap counterpart.
    assert len(no_overlap) == len(with_overlap)
    for plain, overlapped in zip(no_overlap[1:], with_overlap[1:], strict=True):
        assert len(overlapped) > len(plain)
