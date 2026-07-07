#!/usr/bin/env python3
"""chunking.py — V6-10 chunk boundaries for long-entry lexical scoring.

PLAN-wave-e-v6-index task 6 (agentm-memory-index.md). Deterministic,
paragraph-aware chunking against the `entry_meta` table's existing columns
(V6-11, shipped 2026-07-06) — this v0 computes chunks on-the-fly at query
time rather than persisting chunk boundaries as new `entry_meta` columns
(an honest, named scope cut: a persistent chunk-storage schema + chunk-
level vector embeddings is a separate, larger future build spanning
vec_index.py's migration path and the embed/index pipeline, not something
this task's time budget covers).

Why chunking, concretely: task 5's V6-20 eval showed recall gaps traceable
to `_bm25_search`'s fixed 500-char search window — long entries (this
plan's own design-doc.md is one) bury the relevant passage past that
window, so a query whose answer lives at char 3000 scores 0 no matter how
relevant the passage is. Chunking + max-passage scoring (score every chunk,
take the best) fixes exactly this blind spot without changing what a
"result" is (still one path per hit, not a chunk-addressable result — that
finer granularity is deferred with the persistent-storage cut above).

Public API:
  chunk_text(body, *, chunk_chars=CHUNK_CHARS, overlap_chars=OVERLAP_CHARS) -> list[str]
"""
from __future__ import annotations

import re

# Chunk size chosen to comfortably hold a few paragraphs (recall.py's
# existing 500-char window was tuned for "typical entry < 2KB" per its own
# comment; CHUNK_CHARS is deliberately similar so short entries still
# produce exactly one chunk — no behavior change for the common case).
CHUNK_CHARS = 500
# Overlap avoids a relevant sentence being split exactly across a chunk
# boundary and losing signal in both halves.
OVERLAP_CHARS = 100

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def chunk_text(body: str, *, chunk_chars: int = CHUNK_CHARS, overlap_chars: int = OVERLAP_CHARS) -> list[str]:
    """Split `body` into overlapping chunks along paragraph boundaries.

    Paragraphs (blank-line-separated) are packed into chunks up to
    `chunk_chars`; a paragraph longer than `chunk_chars` on its own is
    hard-split (rare for MemoryVault entries, but must not silently drop
    content). Each chunk after the first repeats the trailing
    `overlap_chars` of the previous chunk, so a passage near a boundary
    isn't split without context in either chunk.

    A body shorter than `chunk_chars` returns exactly one chunk (the whole
    body) — no behavior change for typical short entries, matching the
    prior 500-char-window default.
    """
    if not body:
        return [""]
    paragraphs = [p for p in _PARAGRAPH_SPLIT_RE.split(body) if p.strip()]
    if not paragraphs:
        paragraphs = [body]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= chunk_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(para) > chunk_chars:
            # Hard-split an oversized single paragraph rather than drop it.
            start = 0
            while start < len(para):
                chunks.append(para[start:start + chunk_chars])
                start += chunk_chars - overlap_chars if chunk_chars > overlap_chars else chunk_chars
            current = ""
        else:
            current = para
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [body[:chunk_chars]]

    # Apply overlap: prepend the trailing overlap_chars of the previous
    # chunk to each subsequent chunk (first chunk is unmodified).
    overlapped = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap_chars:] if overlap_chars else ""
        overlapped.append((prev_tail + chunks[i]) if prev_tail else chunks[i])
    return overlapped
