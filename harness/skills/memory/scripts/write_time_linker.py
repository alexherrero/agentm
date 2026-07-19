#!/usr/bin/env python3
"""write_time_linker.py — apply the write-time linker's "Related" patch.

PLAN-auto-org-write-time-linking task 3. `vec_index.drain_queue()` calls
`apply()` right after a successful upsert, for any queue record enqueued
with `link=True` (`save.py`'s post-save hook: a single
`vec_index.enqueue(..., link=True)` call, nothing more).

Deliberately deferred to drain time, not save time: a synchronous,
in-the-hot-path call to `embed_text()` for the just-saved note would trigger
a real model load (and, on this machine, a live HuggingFace Hub network
round-trip even with the model already cached) on every single save —
confirmed empirically during this task's own build, when an unrelated
save.py test that had never touched embeddings before started making live
HF Hub requests the moment an early, synchronous version of this module
existed. A `HF_HUB_OFFLINE`-forcing fix was tried and also confirmed broken
(offline mode fails to resolve an already-cached model from disk on this
sentence-transformers/huggingface_hub version — a known revision-resolution
quirk, not something to build a "must never block a save" guarantee on top
of). Deferring to drain sidesteps the whole problem: drain already pays a
real embedding cost for the ordinary vec-index upsert, so reusing that SAME
embedding for linking is free, and the design doc's own framing — "it works
the same way the embedding queue does, so saving stays fast" — is honored
literally. This is a deliberate divergence from the plan's task 3 "What"
text, which frames the nearest-neighbor call as inline in save.py; see the
plan's task 3 status note for the full reasoning trail.

After a link is applied, this also nudges the persisted graph snapshot
(task 2) via `graph_snapshot.rebuild(vault, paths=[rel_path])` — the
targeted-incremental mode task 2 built specifically for this caller, so the
snapshot doesn't go stale until the next weekly full rebuild.

Public API:
  apply(vault_path, rel_path, embedding) -> list[str]  # slugs linked, [] if none
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vec_index  # noqa: E402
from storage_device_local import DeviceLocalBackend  # noqa: E402
from vault_lock import vault_mutex  # noqa: E402

# Named calibration defaults (plan constraint: "named, easily-adjustable
# constants, not hardcoded magic numbers scattered through the logic").
# Provisional starting points, tuned from the connectivity meter once real
# cycles run. 0.70 mirrors notes_link_discovery.py's own
# _DEFAULT_EMBED_MIN_SCORE precedent for the same BGE-large embedding model
# family ("cosine runs hot ~0.3-0.5 even for unrelated prose, so the
# semantic threshold sits well above the TF-IDF one").
LINK_SIMILARITY_FLOOR = 0.70
MAX_RELATED_LINKS = 3

# The weekly link-improvement sweep's (task 4) higher bar for a fully
# deterministic, both-directions, auto-applied link — distinct from (and
# above) LINK_SIMILARITY_FLOOR, which only gates the single-direction,
# write-time suggestion. Below LINK_SIMILARITY_FLOOR: not a candidate at
# all. Between the two: the "ambiguous middle band" the design reserves for
# a budget-capped cheap-model yes/no — not built (see dream.py's
# _stage_link_improvement module note); candidates in that band are simply
# left unlinked, the same outcome the design specifies for "budget spent or
# tier unavailable," just unconditionally rather than conditionally.
CONFIDENT_SIMILARITY_THRESHOLD = 0.85

_RELATED_LINE_RE = re.compile(r"^\*\*Related:\*\* (.+)$", re.MULTILINE)
_RELATED_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FENCE_MARKER_RE = re.compile(r"^```", re.MULTILINE)

# ingest.py's chunk-slug formula: `<doc-slug>-chunk-<i>` (see its `ingest()`
# — the only writer that produces this pattern).
_CHUNK_SLUG_RE = re.compile(r"^(?P<doc>.+)-chunk-\d+$")

# Extra results requested beyond the Related-line cap when querying the
# vector index for link candidates (auto-org part 2 task 5). An ingested
# chunk's very nearest neighbors are overwhelmingly its own batch — the
# sibling chunks and the parent document, near-identical text — and a query
# sized exactly to the cap would return ONLY those, crowding every outward
# candidate out of the result window before `same_ingest_batch` even gets
# to filter them. The headroom keeps outward candidates visible; sized to
# comfortably exceed a typical batch's chunk count. Cheap: a sqlite-vec
# top-k query's cost barely moves between k=4 and k=12.
_NEIGHBOR_QUERY_HEADROOM = 8


def same_ingest_batch(slug_a: str, slug_b: str) -> bool:
    """True if two slugs belong to the same ingest batch (the document note
    + its reading-order chunk notes, per ingest.py's `<doc-slug>-chunk-<i>`
    formula).

    Ingest batches arrive internally linked already — every chunk carries a
    reading-order nav footer plus a backlink to the document — so the
    linker's job for a batch is OUTWARD connections only (the design's own
    words: "Ingest batches from the capture design arrive internally linked
    already. The linker adds their outward connections."). Both linking
    surfaces (write-time `apply()` and the weekly sweep) use this to skip
    intra-batch candidates, which would otherwise dominate every chunk's
    nearest-neighbor list with links the batch already has.

    A hand-written non-ingest slug that happens to end in `-chunk-<N>`
    would false-positive here; the cost is a missed link suggestion for
    that one pair (never a wrong write), accepted for the simplicity of
    matching ingest.py's deterministic formula directly.
    """
    a = _CHUNK_SLUG_RE.match(slug_a)
    b = _CHUNK_SLUG_RE.match(slug_b)
    base_a = a.group("doc") if a else slug_a
    base_b = b.group("doc") if b else slug_b
    return base_a == base_b


def _fenced_ranges(content: str) -> list[tuple[int, int]]:
    """(start, end) char-offset ranges covered by fenced code blocks
    (paired ``` markers). An unterminated final fence extends to
    end-of-string — conservative: better to wrongly treat trailing content
    as fenced than to wrongly mutate inside an unterminated fence."""
    markers = [m.start() for m in _FENCE_MARKER_RE.finditer(content)]
    ranges = [(markers[i], markers[i + 1]) for i in range(0, len(markers) - 1, 2)]
    if len(markers) % 2 == 1:
        ranges.append((markers[-1], len(content)))
    return ranges


def _in_any_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def merge_related_slugs(content: str, new_slugs: list[str]) -> str | None:
    """Merge `new_slugs` into `content`'s `**Related:**` line, creating one
    if absent. Existing slugs are preserved, new ones appended in order,
    de-duplicated, capped at `MAX_RELATED_LINKS` total.

    Returns the updated content, or `None` if nothing would actually
    change (every new slug is already present, or `new_slugs` is empty) —
    the idempotency contract both `apply()` (task 3, re-running on the
    same note) and the weekly sweep (task 4, an older note gaining a new
    neighbor over several cycles) both need: a note's Related line grows
    additively across calls, it never gets a second, duplicate line.

    A `**Related:**`-shaped line inside a fenced code block (a note that
    happens to show markdown syntax as a worked example) is never treated
    as the real line — matched candidates inside a fence are dropped
    before picking one, so a fenced false positive can't get silently
    mutated in place while the note's real body goes unlinked. If more
    than one non-fenced candidate exists (manual edit, corrupted state),
    the LAST one is treated as authoritative — this system only ever
    appends, so the last occurrence is the one it would itself have
    written; earlier ones are left untouched rather than guessed-and-merged.

    Pure function — no I/O. Callers own reading/writing the file.
    """
    if not new_slugs:
        return None
    fenced = _fenced_ranges(content)
    candidates = [m for m in _RELATED_LINE_RE.finditer(content) if not _in_any_range(m.start(), fenced)]
    match = candidates[-1] if candidates else None
    existing = _RELATED_WIKILINK_RE.findall(match.group(1)) if match else []
    merged = list(existing)
    for s in new_slugs:
        if s not in merged:
            merged.append(s)
    merged = merged[:MAX_RELATED_LINKS]
    if merged == existing:
        return None
    related_line = "**Related:** " + ", ".join(f"[[{s}]]" for s in merged)
    if match:
        return content[: match.start()] + related_line + content[match.end() :]
    return content.rstrip("\n") + "\n\n" + related_line + "\n"


def apply(vault_path: Path | str, rel_path: str, embedding: list[float]) -> list[str]:
    """Add up to `MAX_RELATED_LINKS` wikilinks to `rel_path` under a short
    "Related" line, for neighbors above `LINK_SIMILARITY_FLOOR`. Additive
    only — never edits a neighbor note (that's the weekly sweep's
    reciprocal-link case, task 4) and never touches an existing Related
    line's content beyond appending this call's own.

    Returns the candidate slugs found (empty list if no qualifying
    neighbor, the target file no longer exists — e.g. deleted between save
    and drain — or every candidate was already linked, see idempotency
    note below) — NOT necessarily what changed on disk; `merge_related_slugs`
    may find some or all of them already present.

    Idempotent via `merge_related_slugs`: re-finding the same neighbor(s)
    a second time (e.g. drain interrupted between a record's successful
    upsert and the queue file's own rewrite, so a re-drain reprocesses an
    already-linked record) is a no-op, not a duplicate line — matches
    `drain_queue()`'s own contract, "idempotent: re-running drain on a
    stable queue produces the same final state."

    The neighbor query (a DB read) runs outside `vault_mutex` — cheap and
    read-only, no need to serialize it against other vault writers — but
    the target file's content is re-read fresh INSIDE the lock, right
    before the write, and the idempotency + existence checks are re-run
    against that fresh read. Matches `ingest_sweep.py`'s own established
    "resolve outside the lock, re-verify + write inside it" convention
    (see `stage_candidate`'s docstring) — a retroactive review of an
    earlier version of this function found it read-then-locked-then-wrote
    from the pre-lock snapshot, a TOCTOU window where a concurrent writer
    to the same note (another save, an `/memory evolve`) could have its
    own change silently clobbered by this function's write.

    Never raises. The caller (`vec_index.drain_queue`) already recorded
    this entry's upsert as successful before calling here; a link-
    application failure must never retroactively turn that into an error.
    """
    vault = Path(vault_path)
    target = vault / rel_path
    if not target.is_file():
        return []
    try:
        origin_meta = vec_index._extract_meta_from_file(target)
        origin_slug = origin_meta["slug"] or Path(rel_path).stem
        # Headroom beyond the cap so an ingest batch's own siblings can't
        # crowd every outward candidate out of the result window (task 5 —
        # see _NEIGHBOR_QUERY_HEADROOM's comment).
        neighbors = vec_index.nearest(
            vault, embedding,
            k=MAX_RELATED_LINKS + 1 + _NEIGHBOR_QUERY_HEADROOM,
            similarity_floor=LINK_SIMILARITY_FLOOR,
        )
        slugs: list[str] = []
        for path, _sim in neighbors:
            if path == rel_path:
                continue  # defensive self-match filter (see plan task 3 note)
            meta = vec_index._extract_meta_from_file(vault / path)
            neighbor_slug = meta["slug"] or Path(path).stem
            if same_ingest_batch(origin_slug, neighbor_slug):
                continue  # already internally linked (nav + backlink) — outward only
            slugs.append(neighbor_slug)
            if len(slugs) >= MAX_RELATED_LINKS:
                break
        if not slugs:
            return []

        backend = DeviceLocalBackend(root=vault)
        locator = backend.resolve(*Path(rel_path).parts)
        with vault_mutex(vault):
            if not target.is_file():
                return []
            current = target.read_text(encoding="utf-8")
            updated = merge_related_slugs(current, slugs)
            if updated is None:
                return []  # already linked to all these candidates -- idempotent no-op
            backend.write(locator, updated)

        # Keep task 2's persisted graph snapshot current for this one note —
        # the targeted-incremental mode it was built for. Best-effort: a
        # snapshot hiccup must not undo an already-applied link.
        try:
            import graph_snapshot
            graph_snapshot.rebuild(vault, paths=[rel_path])
        except Exception:
            pass

        return slugs
    except Exception:
        return []
