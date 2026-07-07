#!/usr/bin/env python3
"""graph.py — V6-2 deterministic typed-edge knowledge graph extractor.

PLAN-wave-e-v6-index task 4 (agentm-memory-index.md). FABLE-confirmed ADAPT
call (design-doc.md § v6-25-external-thinking-audit): GBrain's *mechanism* —
a deterministic regex/heuristic extraction cascade over `[[wikilinks]]` +
prose + frontmatter, zero LLM calls, A3-safe, no write-gate — with the
gist's dev-shaped verb vocabulary (uses, depends-on, contradicts, caused,
fixed, supersedes) plus agentm's own (decided-in, implements, references),
not GBrain's VC-shaped vocabulary.

The graph indexes over markdown; markdown stays source of truth. This is
one RRF stream (task 5's job to fuse), not the retrieval spine. Multi-hop
traversal is the separate V6-19 browse/discovery verb (out of scope here);
it only joins RRF fusion after this module's own V6-20 eval (see
`scripts/eval_v6_edges.py`) measures edge precision on the real vault and a
future plan's floor decision clears it — this task does not fold traversal
into fusion.

Zero-LLM invariant (A3-safe, non-negotiable per FABLE's V6-2 row): every
function in this module is pure regex/string matching over already-read
file content. No network call, no model invocation, anywhere in the path —
enforced by a red-test that great over this module's source text for any
LLM/API-client import.

Extraction cascade:
  1. Find every `[[wikilink]]` occurrence in the body (frontmatter excluded
     from this pass — frontmatter-only edges are pass 2).
  2. Drop occurrences inside a fenced code block or an inline single-
     backtick code span — a `[[Target]]` inside `` `- relation_type
     [[Target]]` `` is a syntax illustration, not a real edge (the exact
     false-positive class GBrain-shaped extractors must reject to have
     usable precision).
  3. Classify the surviving occurrence's edge type by matching cue phrases
     in a bounded window of prose around the link — first cue that matches
     wins (checked in a fixed priority order); no cue match → "references"
     (the generic-citation default, matching the real vault's own
     distribution — most `[[...]]` uses are plain cross-references).
  4. Pass 2 (frontmatter): a `supersedes:` / `superseded_by:` frontmatter
     field is always a `supersedes` edge, independent of any wikilink
     wrapping (the field may hold a bare path).

Public API:
  extract_edges(vault, rel_path, content) -> list[Edge]
  extract_edges_for_paths(vault, rel_paths) -> list[Edge]  # convenience
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
_FENCE_RE = re.compile(r"^```")

# Cue phrases -> edge type, checked in this priority order; first hit wins.
# Case-insensitive. Each entry searches a bounded window of prose around
# the wikilink occurrence (see _WINDOW_BEFORE / _WINDOW_AFTER below).
_TYPE_CUES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bsupersede[sd]?\b", re.I), "supersedes"),
    (re.compile(r"\bmutually exclusive\b|\bcontradicts?\b|\btension\b", re.I), "contradicts"),
    (re.compile(r"\b(?:the\s+)?\d+m?-?token incident\b|\bincident autopsy\b|\breproduc\w* (?:the|an?) (?:incident|bug)\b", re.I), "caused"),
    (re.compile(r"\bfix(?:ed|es)?\b|\bresolved by\b", re.I), "fixed"),
    (re.compile(r"\bdecided\b|\bresolves? tension\b|\bcalibrat\w*\b|\btension #\d+.{0,20}resolution\b", re.I), "decided-in"),
    (re.compile(r"\bimplements?\b|\bconforms? to\b|\binherits?\b|\bdesign-doc shape\b", re.I), "implements"),
    (
        re.compile(
            r"\bdepends? on\b|\bfloor it (?:builds|preserves|composes)\b|\bgated on\b"
            r"|\bcan'?t ship before\b|\bblocked on\b|\bhard precede\b|\bassumes\b"
            r"|\bprereq(?:uisite)?s?\b|\bsoft dependency\b|\bmust already be in place\b"
            r"|\bgoverning safety constraint\b|\bthe engine it wraps\b",
            re.I,
        ),
        "depends-on",
    ),
    (re.compile(r"\buses\b|\bpairs with\b|\bwrite-authz boundary\b", re.I), "uses"),
)

# Window (chars) searched around each wikilink occurrence for a type cue.
_WINDOW_BEFORE = 160
_WINDOW_AFTER = 90

_DEFAULT_EDGE_TYPE = "references"


@dataclass(frozen=True)
class Edge:
    source_path: str
    target: str
    edge_type: str
    is_edge: bool = True


def _code_span_ranges(line: str) -> list[tuple[int, int]]:
    """Return (start, end) char ranges of inline `code` spans on this line."""
    return [(m.start(), m.end()) for m in _INLINE_CODE_SPAN_RE.finditer(line)]


def _in_any_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def _classify(window_text: str) -> str:
    for pattern, edge_type in _TYPE_CUES:
        if pattern.search(window_text):
            return edge_type
    return _DEFAULT_EDGE_TYPE


def extract_edges(source_path: str, content: str) -> list[Edge]:
    """Extract typed edges from one entry's raw file content (zero LLM calls).

    `source_path` is the vault-relative path (used only as the edge's
    source label — this function does no filesystem I/O itself).
    """
    edges: list[Edge] = []

    # Split into lines, tracking fenced-code-block state, so both fence and
    # inline-code exclusion apply without needing an AST-grade parser.
    lines = content.split("\n")
    in_fence = False
    offset = 0
    line_starts: list[int] = []
    fenced_lines: set[int] = set()
    for i, line in enumerate(lines):
        line_starts.append(offset)
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            fenced_lines.add(i)  # the fence marker line itself is never a link
        elif in_fence:
            fenced_lines.add(i)
        offset += len(line) + 1  # +1 for the split '\n'

    for m in _WIKILINK_RE.finditer(content):
        # Locate which line this match starts on.
        line_idx = 0
        for i in range(len(line_starts) - 1, -1, -1):
            if line_starts[i] <= m.start():
                line_idx = i
                break
        if line_idx in fenced_lines:
            continue
        line = lines[line_idx]
        col = m.start() - line_starts[line_idx]
        if _in_any_range(col, _code_span_ranges(line)):
            continue

        target = m.group(1).strip()
        window = content[max(0, m.start() - _WINDOW_BEFORE): m.end() + _WINDOW_AFTER]
        edge_type = _classify(window)
        edges.append(Edge(source_path=source_path, target=target, edge_type=edge_type))

    # Pass 2 — frontmatter supersedes/superseded_by (independent of the
    # wikilink pass; the field may hold a bare path, no [[...]] wrapping).
    fm_text = _frontmatter_text(content)
    if fm_text:
        for line in fm_text.split("\n"):
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            if key in ("supersedes", "superseded_by"):
                value = value.strip().strip('"').strip("'")
                # Strip [[...]] wrapping if present, to get the bare target.
                wl = _WIKILINK_RE.match(value)
                target = wl.group(1).strip() if wl else value
                if target:
                    edges.append(Edge(source_path=source_path, target=target, edge_type="supersedes"))

    return edges


def _frontmatter_text(content: str) -> str | None:
    if not content.startswith("---\n"):
        return None
    end = content.find("\n---\n", 4)
    if end == -1:
        return None
    return content[4:end]


def extract_edges_for_paths(vault: Path, rel_paths: list[str]) -> list[Edge]:
    """Convenience: read each path under `vault` and extract its edges.

    Read failures (missing/unreadable file) are skipped silently — this
    mirrors the read-tolerant pattern already used by heat_policy.py and
    lifecycle.py (best-effort, never blocks on one bad file).
    """
    out: list[Edge] = []
    for rel in rel_paths:
        path = vault / rel
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.extend(extract_edges(rel, content))
    return out
