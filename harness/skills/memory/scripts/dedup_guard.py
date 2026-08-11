#!/usr/bin/env python3
"""dedup_guard.py — the write-time dedup guard (auto-org part 3, task 2).

Before a note is written, an EXACT content-fingerprint match against an
existing entry means the arriving note reinforces the existing one instead
of creating a duplicate file: the existing note's `occurrences` count and
`updated` stamp bump, and no new file appears. A suffix now only ever
means genuinely different content sharing a title.

Exact-match only, by the plan's own Locked design call ("Fingerprint-exact
collapses are deterministic; fuzzy merges need a model verdict. No
exception either direction."): a write-time NEAR-match auto-reinforce
would silently discard the arriving note's real differences without the
verdict the locked call requires — and computing the embedding a
near-match needs would reintroduce the synchronous model-load-per-save
regression part 2 eliminated. Near-duplicates still write normally and
flow to the weekly cluster pass (task 3), where the verdict/needs-your-eye
machinery owns them.

One lookup surface remains:

  - `find_inbox_duplicate` — capture's staging writes. A direct
    frontmatter scan of `personal/_inbox/` (small by design — triage
    drains it).

Its sibling `find_vault_duplicate`, which guarded permanent-memory writes
(`save_entry`), went with the vector index: it resolved a fingerprint
through that index's own `entry_meta` table, and no other fingerprint->path
lookup exists in the tree. A scan is not a substitute at 8k+ notes on every
save, so permanent-memory writes no longer dedup at write time and the
weekly cluster pass owns those duplicates. See the amendment log in
`wiki/designs/agentm-rescope-week1-experiment.md`.

`reinforce` is the one mutation: occurrences+1 (absent = 1) and a fresh
`updated` stamp, patched in place. Callers hold `vault_mutex` around the
find+reinforce pair — the same lock-across-resolve+write convention
`capture()` and `save_entry()` already follow — so two concurrent
identical writes can't both miss the guard.
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import atomic_write  # noqa: E402

_OCCURRENCES_RE = re.compile(r"^occurrences: (\d+)$", re.MULTILINE)
_UPDATED_RE = re.compile(r"^updated: .*$", re.MULTILINE)
_FINGERPRINT_LINE_RE = re.compile(r"^fingerprint: (\S+)$", re.MULTILINE)
_STATUS_LINE_RE = re.compile(r"^status: (\S+)$", re.MULTILINE)

def _frontmatter_span(content: str) -> tuple[int, int] | None:
    """(start, end) offsets of the frontmatter text between the `---`
    markers, or None if the file has no frontmatter block."""
    if not content.startswith("---\n"):
        return None
    end = content.find("\n---\n", 4)
    if end == -1:
        return None
    return 4, end


def file_fingerprint(path: Path) -> str | None:
    """The `fingerprint:` frontmatter value of `path`, or None (missing
    file, no frontmatter, no fingerprint line). Best-effort, never raises.

    NOTE: this is the STORED value, which lags any body edit made outside
    `save_entry` (a manual rewrite, an applied link mutation). Duplicate
    matching must use `live_content_fingerprint` instead — matching on a
    stale stored value could reinforce a note that no longer says what the
    arriving note says, silently discarding real content (the exact
    false-positive failure mode the plan rules out)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    span = _frontmatter_span(content)
    if span is None:
        return None
    m = _FINGERPRINT_LINE_RE.search(content[span[0]:span[1]])
    return m.group(1) if m else None


def live_content_fingerprint(path: Path) -> str | None:
    """`compute_fingerprint` over `path`'s CURRENT body (frontmatter
    stripped) — the authoritative duplicate-match key. Best-effort, never
    raises."""
    from fingerprint import compute_fingerprint  # same skill dir

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    span = _frontmatter_span(content)
    body = content[span[1] + 5:] if span is not None else content
    return compute_fingerprint(body)


def has_frontmatter_field(path: Path, field: str) -> bool:
    """True if `path`'s frontmatter carries a `<field>:` line. Used by
    capture's guard to refuse a reinforce that would silently discard
    arriving metadata (a link resend's `source_url` — the ingest sweep's
    trigger — deduping into a plain-text candidate that lacks it)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    span = _frontmatter_span(content)
    if span is None:
        return False
    return re.search(rf"^{re.escape(field)}: ", content[span[0]:span[1]], re.MULTILINE) is not None


def _file_status(path: Path) -> str | None:
    """The `status:` frontmatter value of `path`, or None. Best-effort."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    span = _frontmatter_span(content)
    if span is None:
        return None
    m = _STATUS_LINE_RE.search(content[span[0]:span[1]])
    return m.group(1) if m else None


def find_inbox_duplicate(vault_path: Path | str, fingerprint: str) -> Path | None:
    """Absolute path of an inbox candidate whose fingerprint matches, or
    None. A plain scan — the inbox is a small staging area by design."""
    inbox = Path(vault_path) / "personal" / "_inbox"
    if not inbox.is_dir():
        return None
    for md in sorted(inbox.glob("*.md")):
        if live_content_fingerprint(md) == fingerprint and _file_status(md) == "inbox":
            # status: inbox only — a triaged tombstone (expired / promoted /
            # triage_rejected / ingest_staged...) is archived-in-place, and
            # reinforcing it would drop the re-capture without it ever
            # re-entering triage. The re-capture writes fresh instead.
            return md
    return None


def reinforce(path: Path, *, today: str | None = None) -> int:
    """Bump `path`'s `occurrences` count (absent = 1 -> 2) and stamp
    `updated:` with today's date, in place, atomically. Returns the new
    occurrence count. Caller holds `vault_mutex`."""
    content = path.read_text(encoding="utf-8")
    span = _frontmatter_span(content)
    if span is None:
        raise ValueError(f"cannot reinforce a file with no frontmatter: {path}")
    today = today or datetime.date.today().isoformat()
    fm = content[span[0]:span[1]]

    m = _OCCURRENCES_RE.search(fm)
    if m:
        count = int(m.group(1)) + 1
        fm = fm[:m.start()] + f"occurrences: {count}" + fm[m.end():]
    else:
        count = 2
        # Insert right after the fingerprint line when present (matching
        # save.py's FRONTMATTER_FIELD_ORDER placement), else append.
        fp_m = _FINGERPRINT_LINE_RE.search(fm)
        if fp_m:
            fm = fm[:fp_m.end()] + f"\noccurrences: {count}" + fm[fp_m.end():]
        else:
            fm = fm.rstrip("\n") + f"\noccurrences: {count}"

    u_m = _UPDATED_RE.search(fm)
    if u_m:
        fm = fm[:u_m.start()] + f"updated: {today}" + fm[u_m.end():]
    else:
        # Fresh insert lands after `created:` when present — `updated`'s
        # slot in save.py's FRONTMATTER_FIELD_ORDER — so the field-order
        # lint stays clean on entries that carry the ordered schema.
        # (save_entry-written entries always have `updated` already; this
        # branch serves capture's schema-lighter inbox candidates.)
        c_m = re.search(r"^created: .*$", fm, re.MULTILINE)
        if c_m:
            fm = fm[:c_m.end()] + f"\nupdated: {today}" + fm[c_m.end():]
        else:
            fm = fm.rstrip("\n") + f"\nupdated: {today}"

    atomic_write(path, content[:span[0]] + fm + content[span[1]:])
    return count
