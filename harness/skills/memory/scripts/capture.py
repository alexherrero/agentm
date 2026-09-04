#!/usr/bin/env python3
"""capture.py — the front door that files a capture at its class directory
(`designs/friday/agentm-capture.md`, capture-front-door plan task 2).

`memory_append` (save.py's `save_entry`) writes straight to permanent
memory and validates `kind` as kebab-case — `_inbox` fails that validation
by construction (its leading underscore), which is the standing convention
that keeps staged items structurally distinct from `save_entry`'s
validated destinations. This module is the second front door: every write
here lands at the class directory the contract routes it to, marked `unfiled`
at low filing confidence (filing v2, the write path) — the metadata is the inbox — and never
goes through `save_entry`/`_validate_path_segment` at all.

Write path: the resolve-then-write sequence runs under `vault_lock.
vault_mutex` (matching `save_entry`'s convention), with the write itself
via `vault_lock.atomic_write` (temp file in the same directory, fsync,
atomic rename) — genuinely atomic per-file and, with the mutex held
across resolution, collision-safe against a second concurrent caller
too. `reflect.py`'s existing `_save_candidate_to_inbox` does a raw
`write_bytes()` with neither guarantee. Multiple transports write into
`_inbox/` concurrently (the Drive connector, the Obsidian Web Clipper,
this module, and the future ingest sweep) and Data Integrity is a named
Quality Attribute of the capture design, so this module defaults to the
safer, genuinely-atomic-and-serialized primitive rather than matching
reflection's current pattern. See the plan's own Constraints section for
the full reasoning and the one-line-reversal note if the operator
prefers matching `reflect.py` instead. (A retroactive /review before the
release cut found the mutex was missing on first ship — two concurrent
callers resolving the same free slug before either wrote would silently
overwrite one candidate with the other, contradicting this contract;
fixed before release, never shipped to a tagged version.)

Every call returns a `CaptureResult` — success or failure is always
explicit, never a silent drop (the design's own reliability contract:
"The system alerts you immediately if a capture fails").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import LockTimeout  # noqa: E402
from volume_gate import VolumeCapRefused  # noqa: E402

_KNOWN_KINDS = ("capture", "idea")


@dataclass(frozen=True)
class CaptureResult:
    success: bool
    path: "Path | None" = None
    slug: "str | None" = None
    error: "str | None" = None
    # True when the write-time dedup guard (auto-org part 3 task 2) matched
    # an existing inbox candidate by exact content fingerprint and
    # reinforced it (occurrences + updated bump) instead of writing a new
    # file — `path`/`slug` then name the EXISTING candidate.
    deduplicated: bool = False


def _iso(now: datetime) -> str:
    """Format `now` as full ISO8601 — mirrors reflect.py's `_utcnow_iso()`
    shape. A chat-surface caller's estimate of `now` gets corrected later
    by the ingest sweep's `captured:` re-stamp (capture part 3); this
    module always writes whatever clock time it's given (the real one by
    default, an injected one in tests)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.replace(microsecond=0).isoformat()


def _slugify(content: str, *, now: datetime) -> str:
    """A timestamp-based default slug when the caller doesn't supply one —
    unique enough in practice that the collision path below is a rare
    resend/race, not the common case."""
    return f"capture-{now.strftime('%Y%m%dT%H%M%S')}"


def _resolve_target(inbox_dir: Path, slug: str) -> Path:
    """Resolve the write target, appending a numeric suffix on collision —
    mirrors `reflect.py::_save_candidate_to_inbox`'s existing convention.
    The Drive connector can create files but never update/delete them, so
    a resend landing twice as near-duplicate candidates is an accepted,
    designed-for case (inbox triage's dedup handles it later)."""
    target = inbox_dir / f"{slug}.md"
    if not target.exists():
        return target
    n = 1
    while True:
        candidate = inbox_dir / f"{slug}-{n}.md"
        if not candidate.exists():
            return candidate
        n += 1


def capture(
    vault_path: "Path | str",
    content: str,
    *,
    kind: str = "capture",
    slug: "str | None" = None,
    source: "str | None" = None,
    surface: "str | None" = None,
    tags: "list[str] | None" = None,
    instructions: "str | None" = None,
    source_url: "str | None" = None,
    now: "datetime | None" = None,
    lock_timeout: float = 10.0,
) -> CaptureResult:
    """File one candidate at the class directory the contract routes its type
    to, as an `unfiled` note at low filing confidence (filing v2, the write
    path). A plain capture takes the contract's default type; `kind="idea"`
    files as `type: idea`. The metadata is the inbox: `status: unfiled` is
    what the enrichment pass and the ingest sweep drain, `filing_confidence:
    low` is what the needs-review reading selects on. Nothing stages in a
    directory any more. Never raises on a write failure — returns a
    `CaptureResult` with `success=False` and the error message instead, so
    a caller (the MCP tool, the CLI verb) always has an explicit outcome to
    relay back to the operator.

    `source` here is the caller's surface tag (`cli`, `mcp`, `clipper`, a
    connector) and is written as `via:`; the contract's `source:` field
    carries the transport — `external-fetch` for a link, `operator-direct`
    otherwise.

    `instructions` is the security-boundary field (task 5's invariant):
    this function stores exactly the string it's given here, verbatim,
    from this call's own explicit argument — it never inspects or parses
    `content` to derive one. A caller that populates `instructions` from
    anything other than the operator's own capture-time text breaks that
    invariant at the call site, not here; this function's contract is
    simply "store what you were handed, nothing inferred."

    An exact repeat reinforces the note already home (occurrences + updated
    bump, no new file) unless the arrival carries act-relevant metadata the
    twin lacks — a `source_url` (the ingest sweep's trigger) or an
    `instructions` string — in which case it files fresh beside it. A twin
    that is no longer live (a tombstone, a superseded note) is never a
    reinforce target.

    `lock_timeout` passes through to the vault mutex the writer takes.
    """
    if kind not in _KNOWN_KINDS:
        return CaptureResult(success=False, error=f"unknown kind {kind!r}; expected one of {_KNOWN_KINDS}")
    if not content or not content.strip():
        return CaptureResult(success=False, error="content must be non-empty")

    try:
        vault = Path(vault_path)
        if not vault.is_dir():
            return CaptureResult(success=False, error=f"vault path does not exist: {vault}")

        now = now or datetime.now(timezone.utc)
        resolved_slug = _kebab(slug or _slugify(content, now=now))
        import dedup_guard  # same skill dir
        import filing_engine  # same skill dir

        title = content.strip().splitlines()[0].strip()[:120]
        extra = {"captured": _iso(now), "via": source, "surface": surface, "instructions": instructions}
        # Decide, then write; when a concurrent writer lands on the settled
        # name between the two, decide again against the disk — the next
        # pass sees the newcomer (a twin to reinforce, or a namesake to
        # settle past with the `~dup` mark). The writer's own guard under
        # its mutex is what makes the loser lose loudly instead of clobbering.
        for _attempt in range(64):
            decision = filing_engine.decide(
                vault, title=title, body=content, slug=resolved_slug,
                type_hint="idea" if kind == "idea" else None, confidence="LOW",
                source="external-fetch" if source_url else "operator-direct",
            )
            if decision.op == "noop":
                twin = vault / decision.dest_rel
                arriving_adds_metadata = (
                    (source_url and not dedup_guard.has_frontmatter_field(twin, "source_url"))
                    or (instructions and not dedup_guard.has_frontmatter_field(twin, "instructions"))
                )
                if _reinforceable(twin) and not arriving_adds_metadata:
                    dedup_guard.reinforce(twin, today=now.date().isoformat())
                    return CaptureResult(success=True, path=twin, slug=twin.stem, deduplicated=True)
                # Files fresh beside the twin: the engine's own settling, asked
                # with a fingerprint that matches nothing so the occupied name
                # yields the next `~dup` mark rather than the twin itself.
                dest, _flags = filing_engine._settle_dest(vault, decision.class_dir, resolved_slug, "")
                decision.op, decision.dest_rel, decision.related = "add", dest, None
            try:
                written = filing_engine.apply(
                    vault, decision, body=content, tags=list(tags or []), title=title,
                    source_url=source_url, status="unfiled", extra=extra,
                )
            except FileExistsError:
                continue
            return CaptureResult(success=True, path=written, slug=written.stem)
        return CaptureResult(success=False, error="could not settle a free name: the vault is being written faster than this capture can decide")
    except OSError as e:
        return CaptureResult(success=False, error=f"write failed: {e}")
    except LockTimeout as e:
        return CaptureResult(success=False, error=f"vault busy: {e}")
    except VolumeCapRefused as e:
        # The gate's own words, verbatim: the count, the cap, the edit that
        # raises it. A refused capture is an outcome the caller sees, never
        # a note that quietly did not appear.
        return CaptureResult(success=False, error=str(e))


def _kebab(slug: str) -> str:
    """The writer's slug contract is kebab-case; a timestamped default slug
    (`capture-20260718T120000`) and a caller's free-form slug both fold to it."""
    return re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "capture"


_DEAD_STATUSES = frozenset({"expired", "deleted", "superseded", "archived", "promoted", "ingest_duplicate"})


def _reinforceable(twin: Path) -> bool:
    """A live note only. A tombstone the triage or the ingest sweep left in
    place, or a note a later one superseded, keeps its record and never
    absorbs a fresh capture."""
    import dedup_guard  # same skill dir
    status = dedup_guard._file_status(twin)
    if status in _DEAD_STATUSES:
        return False
    try:
        return "lifecycle: superseded" not in twin.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    except (OSError, UnicodeDecodeError):
        return False


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-capture",
        description=(
            "Capture a thought, link, or idea into MemoryVault's staging inbox "
            "(filed at its class as `status: unfiled`). Canonical Python implementation behind "
            "/memory capture (see SKILL.md)."
        ),
    )
    parser.add_argument("content", help="the captured text (a thought, or a link + note)")
    parser.add_argument("--vault-path", help="vault root (default: $MEMORY_VAULT_PATH env var)")
    parser.add_argument("--kind", choices=_KNOWN_KINDS, default="capture")
    parser.add_argument("--slug", help="override the default timestamp-based slug")
    parser.add_argument("--source", help="the transport, e.g. 'cli', 'clipper'")
    parser.add_argument("--surface", help="the device/surface, e.g. 'phone', 'desktop'")
    parser.add_argument("--tags", nargs="*", default=None)
    parser.add_argument("--instructions", help="an operator-typed action to run after absorb")
    parser.add_argument("--source-url", help="the link this capture is about, if any")
    return parser.parse_args(argv[1:])


def _resolve_vault(cli_arg: "str | None") -> "Path | None":
    """arg → $MEMORY_VAULT_PATH. Deliberately does NOT import harness_memory:
    kernel toolkit scripts under harness/skills/memory/scripts/ are invoked
    as subprocesses by the harness_memory bridge and must never import it
    back (V5-5 LC-8 bridge extension, enforced by
    scripts/check-one-way-imports.py's lc8-bridge rule). The bridge — or any
    other caller — resolves `harness_memory.vault_path()` and exports it as
    $MEMORY_VAULT_PATH before invoking this script. Same convention as
    `ideas_promote.py::_resolve_vault_root`."""
    if cli_arg:
        p = Path(cli_arg)
        return p if p.is_dir() else None
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)
    vault = _resolve_vault(args.vault_path)
    if vault is None:
        print("[capture] no vault resolved — pass --vault-path or configure MEMORY_VAULT_PATH", file=sys.stderr)
        return 2
    result = capture(
        vault, args.content, kind=args.kind, slug=args.slug, source=args.source or "cli",
        surface=args.surface, tags=args.tags, instructions=args.instructions,
        source_url=args.source_url,
    )
    if result.success:
        print(f"captured: {result.path}")
        return 0
    print(f"[capture] failed: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
