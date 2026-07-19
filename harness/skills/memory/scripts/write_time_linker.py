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


def apply(vault_path: Path | str, rel_path: str, embedding: list[float]) -> list[str]:
    """Add up to `MAX_RELATED_LINKS` wikilinks to `rel_path` under a short
    "Related" line, for neighbors above `LINK_SIMILARITY_FLOOR`. Additive
    only — never edits a neighbor note (that's the weekly sweep's
    reciprocal-link case, task 4) and never touches an existing Related
    line's content beyond appending this call's own.

    Returns the slugs linked (empty list if no qualifying neighbor, the
    target file no longer exists — e.g. deleted between save and drain —
    or the note already carries a Related line, see idempotency note
    below).

    Idempotent: if `rel_path` already has a `**Related:**` line by the
    time the write actually happens, this is a no-op (returns `[]`,
    doesn't touch the file). Matters because `drain_queue()`'s own
    contract is "idempotent: re-running drain on a stable queue produces
    the same final state" — without this check, a queue record that gets
    reprocessed (e.g. drain interrupted between a record's successful
    upsert and the queue file's own rewrite) would append a second,
    duplicate Related line rather than leaving the already-linked note
    alone. The write-time linker only ever adds ONE Related line per
    note; a later re-evaluation of the SAME note against newer neighbors
    is the weekly sweep's job (task 4), not a re-run of this function.

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
        neighbors = vec_index.nearest(
            vault, embedding, k=MAX_RELATED_LINKS + 1, similarity_floor=LINK_SIMILARITY_FLOOR
        )
        slugs: list[str] = []
        for path, _sim in neighbors:
            if path == rel_path:
                continue  # defensive self-match filter (see plan task 3 note)
            meta = vec_index._extract_meta_from_file(vault / path)
            slugs.append(meta["slug"] or Path(path).stem)
            if len(slugs) >= MAX_RELATED_LINKS:
                break
        if not slugs:
            return []
        related_line = "**Related:** " + ", ".join(f"[[{s}]]" for s in slugs)

        backend = DeviceLocalBackend(root=vault)
        locator = backend.resolve(*Path(rel_path).parts)
        with vault_mutex(vault):
            if not target.is_file():
                return []
            current = target.read_text(encoding="utf-8")
            if "**Related:**" in current:
                return []  # already linked -- idempotent no-op
            updated = current.rstrip("\n") + "\n\n" + related_line + "\n"
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
