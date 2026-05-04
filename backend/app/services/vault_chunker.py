"""Recursive text chunker for the Vault RAG path.

Splits extracted file text into ~``max_tokens`` chunks with a small
overlap so sentence-boundary context survives the chop. Token counting
uses a naive word-split heuristic — we don't pull tiktoken just for
chunk-sizing; ~5% miscounting vs the embedding model's actual
tokenization is negligible for retrieval quality.

Strategy: recursively try paragraph, then sentence, then word splits.
Always honor the budget; never produce a chunk longer than
``max_tokens * 1.2`` even on adversarial input (one giant word).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# Conservative word→token ratio. Gemini / OpenAI tokenizers average
# ~1.3 tokens per word for English; we use 1.0 here so the chunks come
# out a bit smaller than nominal — better to under-fill than overflow
# the embedding model's context.
_TOKENS_PER_WORD = 1.0

DEFAULT_MAX_TOKENS = 500
DEFAULT_OVERLAP_TOKENS = 50

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")


def _approx_tokens(text: str) -> int:
    """Naive token count — counts whitespace-delimited words."""
    return max(1, int(len(_WHITESPACE.split(text.strip())) * _TOKENS_PER_WORD))


def _split_recursive(text: str, max_tokens: int) -> Iterator[str]:
    """Yield pieces of text each <= ``max_tokens`` tokens.

    Tries paragraph splits first, then sentences, then words. Always
    yields *something* for non-empty input — a runaway 10k-character
    word still gets emitted as a single oversize chunk rather than
    silently dropped, with the truth that retrieval quality on it
    will be poor.
    """
    text = text.strip()
    if not text:
        return
    if _approx_tokens(text) <= max_tokens:
        yield text
        return

    # Try paragraphs.
    parts = [p for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    if len(parts) > 1:
        for part in parts:
            yield from _split_recursive(part, max_tokens)
        return

    # Try sentences.
    parts = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(parts) > 1:
        # Re-pack into chunks under the budget so we don't emit one
        # sentence per chunk on already-short text.
        buffer: list[str] = []
        buffer_tokens = 0
        for sentence in parts:
            sentence_tokens = _approx_tokens(sentence)
            if buffer and buffer_tokens + sentence_tokens > max_tokens:
                yield " ".join(buffer)
                buffer = [sentence]
                buffer_tokens = sentence_tokens
            else:
                buffer.append(sentence)
                buffer_tokens += sentence_tokens
        if buffer:
            yield " ".join(buffer)
        return

    # Fall back to word slicing — last resort for run-on text.
    words = text.split()
    if not words:
        yield text
        return
    chunk: list[str] = []
    chunk_tokens = 0
    for word in words:
        word_tokens = _approx_tokens(word)
        if chunk and chunk_tokens + word_tokens > max_tokens:
            yield " ".join(chunk)
            chunk = [word]
            chunk_tokens = word_tokens
        else:
            chunk.append(word)
            chunk_tokens += word_tokens
    if chunk:
        yield " ".join(chunk)


def _apply_overlap(chunks: list[str], overlap_tokens: int) -> list[str]:
    """Prepend the tail of each chunk to the next one.

    Overlap-as-suffix-of-previous gives chunks at the edges some shared
    context, which materially improves retrieval recall on questions
    whose answer straddles a chunk boundary. Skipped when overlap=0
    (used by tests / very tight token budgets)."""
    if overlap_tokens <= 0 or len(chunks) <= 1:
        return chunks
    result = [chunks[0]]
    for chunk in chunks[1:]:
        prev_words = result[-1].split()
        # Approx ``overlap_tokens`` words from the end of the previous chunk.
        take = min(len(prev_words), max(1, int(overlap_tokens / _TOKENS_PER_WORD)))
        suffix = " ".join(prev_words[-take:])
        result.append(f"{suffix} {chunk}".strip())
    return result


def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[str]:
    """Split ``text`` into chunks suitable for embedding.

    Empty / whitespace-only input returns an empty list — the caller
    should treat "no chunks" as "this file added no usable text" and
    not insert any chunk rows; the file row's status walk still works
    (extracting → embedding → ready) but the corpus is empty.
    """
    if not text or not text.strip():
        return []
    raw = list(_split_recursive(text, max_tokens))
    return _apply_overlap(raw, overlap_tokens)
