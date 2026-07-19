#!/usr/bin/env python3
"""fingerprint.py — stable content fingerprint for memory entries.

PLAN-auto-org-dedup-and-lint task 1. The `fingerprint` frontmatter field
has existed as an optional caller-supplied passthrough since V6-11
(`save.py` threads it; `vec_index.py` reserves an `entry_meta` column
commented "NULL until a writer populates it"), but nothing anywhere in
this codebase ever COMPUTED one. This module is that writer: a stable
sha256 over a note's normalized body text, wired into `save_entry()` so
every new note carries a real value automatically.

Normalization is deliberately conservative (the plan's own risk note:
favor false-negative over false-positive — a missed duplicate is
recoverable next cycle; a wrongly-collapsed distinct note is the worse
failure mode). It catches formatting-only divergence and nothing more:

  - line endings normalized (CRLF/CR -> LF)
  - each line stripped of leading/trailing whitespace
  - internal whitespace runs collapsed to one space
  - blank lines dropped
  - casefolded (aggressive lowercase, Unicode-correct)

It deliberately does NOT strip punctuation, markdown syntax, or
wikilinks — two notes differing in a link target or a negation particle
are genuinely different content, and collapsing them would be the exact
false-positive failure mode the plan warns against.

Callers: `save_entry()` (auto-populate, task 1), the write-time dedup
guard (task 2), the cluster-aware weekly dedup + suffix-backlog drain
(tasks 3/6).

Public API:
  compute_fingerprint(body: str) -> str   # 64-char hex sha256
  normalize_body(body: str) -> str        # the exact normalized form hashed
"""

from __future__ import annotations

import hashlib
import re

_WHITESPACE_RUN_RE = re.compile(r"[ \t\f\v]+")


def normalize_body(body: str) -> str:
    """The exact normalized text `compute_fingerprint` hashes — exposed so
    tests (and any future near-match tooling) can inspect the canonical
    form rather than reverse-engineering it from hash equality."""
    lines = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        collapsed = _WHITESPACE_RUN_RE.sub(" ", line.strip())
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines).casefold()


def compute_fingerprint(body: str) -> str:
    """Stable sha256 hex digest over `normalize_body(body)`. Two notes with
    the same fingerprint are formatting-variants of the same content; two
    different fingerprints say nothing (near-duplicates are the vector
    index's job, not this hash's)."""
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()
