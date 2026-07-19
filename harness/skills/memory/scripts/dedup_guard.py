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

Two lookup surfaces, one per write path:

  - `find_vault_duplicate` — permanent-memory writes (`save_entry`).
    Looks up `entry_meta.fingerprint` (the column task 1 gave a universal
    writer) via the vec-index DB, then RE-VERIFIES against the live file
    (the index lags drain; a stale row must never cause a reinforce
    against content that has since changed — same live-revalidation
    posture `drain_queue` itself takes). Graceful-skip when sqlite-vec is
    unavailable: returns None, the write proceeds, the weekly pass caches
    the miss. Favor false-negative over false-positive throughout (the
    plan's own risk rule).
  - `find_inbox_duplicate` — capture's staging writes. A direct
    frontmatter scan of `personal/_inbox/` (small by design — triage
    drains it; the index deliberately never contains inbox candidates).

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

# The only statuses a duplicate may reinforce into. A review of this
# guard's first version found the matching was status-blind: a re-capture
# of a triage-expired thought reinforced the tombstone (status stayed
# `expired`, so it could never re-enter triage — violating capture.py's
# "a capture is never silently dropped" contract), and a re-save of
# `status: deleted`/`superseded` content was swallowed into a note recall
# filters out. A dead note is not a duplicate target; the arriving note
# writes normally and lives its own life.
_REINFORCEABLE_STATUSES = frozenset({"active", "inbox"})


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


def _is_reinforceable(path: Path) -> bool:
    """A note may only absorb a duplicate when it is alive (status in
    `_REINFORCEABLE_STATUSES`) and not a curated always-load standing rule
    — the design's exemption list applies to the MATCH TARGET, not just
    the arriving note (review-caught: the first version enforced it only
    on the arriving side)."""
    if "_always-load" in path.parts:
        return False
    return _file_status(path) in _REINFORCEABLE_STATUSES


def find_vault_duplicate(vault_path: Path | str, fingerprint: str) -> str | None:
    """Vault-relative path of an existing entry whose LIVE fingerprint
    equals `fingerprint` and which is a legitimate reinforce target
    (alive, not curated — see `_is_reinforceable`), or None. Index-first
    (one SQL lookup), then live-file re-verification; graceful None when
    sqlite-vec is absent."""
    import vec_index  # local import — graceful-skip machinery lives there

    vault = Path(vault_path)
    conn = vec_index._open_index(vault)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT path FROM entry_meta WHERE fingerprint = ?", (fingerprint,)
        ).fetchall()
    finally:
        conn.close()
    for (rel_path,) in rows:
        live = vault / rel_path
        if live_content_fingerprint(live) == fingerprint and _is_reinforceable(live):
            return rel_path
    return None


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
