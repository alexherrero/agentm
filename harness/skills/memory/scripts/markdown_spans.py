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

_FENCE_OPEN_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


def fenced_ranges(content: str) -> list[tuple[int, int]]:
    """(start, end) char-offset ranges covered by fenced code blocks.

    A fence opens on a line of three or more backticks or tildes, indented at
    most three spaces; a backtick fence's info string holds no backtick. It
    closes on a line of the same character, at least as long, with nothing
    after it but spaces. Anything else inside is content, so a three-backtick
    line inside a four-backtick or tilde fence does not end it. An
    unterminated final fence extends to end-of-string — conservative: better
    to wrongly treat trailing content as fenced than to wrongly mutate inside
    an unterminated fence."""
    ranges: list[tuple[int, int]] = []
    start, fence, pos = None, "", 0
    for line in content.split("\n"):
        text = line.rstrip("\r")
        if start is None:
            m = _FENCE_OPEN_RE.fullmatch(text)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                start, fence = pos, m.group(1)
        elif re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{%d,}[ \t]*" % len(fence), text):
            ranges.append((start, pos + len(text)))
            start = None
        pos += len(line) + 1
    if start is not None:
        ranges.append((start, len(content)))
    return ranges


def in_any_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)
