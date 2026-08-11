#!/usr/bin/env python3
"""markdown_spans.py — locate the regions of a note that markup edits must skip.

These helpers lived in `write_time_linker.py`, which generated `**Related:**`
lines from vector-index nearest-neighbor queries. That module went with the
vector stack (see `wiki/designs/agentm-rescope-week1-experiment.md`); the
markup it wrote did not. Notes across the vault still carry Related lines, and
`dream.py` and `lint.py` still have to find them, count their wikilinks, and
strip them — none of which ever needed an embedding.

Nothing here generates a link. This module recognizes markup that already
exists, so callers editing a note can leave fenced code blocks alone: a
wikilink shown as a worked example inside a fence is documentation, not a real
link, and rewriting it would corrupt the very thing it documents.
"""

from __future__ import annotations

import re

# The Related line and the wikilinks inside it.
RELATED_LINE_RE = re.compile(r"^\*\*Related:\*\* (.+)$", re.MULTILINE)
RELATED_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

_FENCE_MARKER_RE = re.compile(r"^```", re.MULTILINE)


def fenced_ranges(content: str) -> list[tuple[int, int]]:
    """(start, end) char-offset ranges covered by fenced code blocks
    (paired ``` markers). An unterminated final fence extends to
    end-of-string — conservative: better to wrongly treat trailing content
    as fenced than to wrongly mutate inside an unterminated fence."""
    markers = [m.start() for m in _FENCE_MARKER_RE.finditer(content)]
    ranges = [(markers[i], markers[i + 1]) for i in range(0, len(markers) - 1, 2)]
    if len(markers) % 2 == 1:
        ranges.append((markers[-1], len(content)))
    return ranges


def in_any_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)
