#!/usr/bin/env python3
"""capture.py — the staging-only front door for `personal/_inbox/`
(`designs/friday/agentm-capture.md`, capture-front-door plan task 2).

`memory_append` (save.py's `save_entry`) writes straight to permanent
memory and validates `kind` as kebab-case — `_inbox` fails that validation
by construction (its leading underscore), which is the standing convention
that keeps staged items structurally distinct from `save_entry`'s
validated destinations. This module is the second front door: every write
here lands in `personal/_inbox/`, never in permanent memory, and never
goes through `save_entry`/`_validate_path_segment` at all.

Write path: `vault_lock.atomic_write` directly (temp file in the same
directory, fsync, atomic rename) — genuinely atomic per-file, unlike
`reflect.py`'s existing `_save_candidate_to_inbox`, which does a raw
`write_bytes()` with no atomic-write guarantee. Multiple transports write
into `_inbox/` concurrently (the Drive connector, the Obsidian Web
Clipper, this module, and the future ingest sweep) and Data Integrity is
a named Quality Attribute of the capture design, so this module defaults
to the safer, genuinely-atomic primitive rather than matching reflection's
current non-atomic pattern. See the plan's own Constraints section for the
full reasoning and the one-line-reversal note if the operator prefers
matching `reflect.py` instead.

Every call returns a `CaptureResult` — success or failure is always
explicit, never a silent drop (the design's own reliability contract:
"The system alerts you immediately if a capture fails").
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import atomic_write  # noqa: E402

_KNOWN_KINDS = ("capture", "idea")


@dataclass(frozen=True)
class CaptureResult:
    success: bool
    path: "Path | None" = None
    slug: "str | None" = None
    error: "str | None" = None


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
) -> CaptureResult:
    """Write one candidate to `personal/_inbox/<slug>.md`. Never raises on a
    write failure — returns a `CaptureResult` with `success=False` and the
    error message instead, so a caller (the MCP tool, the CLI verb) always
    has an explicit outcome to relay back to the operator.

    `instructions` is the security-boundary field (task 5's invariant):
    this function stores exactly the string it's given here, verbatim,
    from this call's own explicit argument — it never inspects or parses
    `content` to derive one. A caller that populates `instructions` from
    anything other than the operator's own capture-time text breaks that
    invariant at the call site, not here; this function's contract is
    simply "store what you were handed, nothing inferred."
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
        resolved_slug = slug or _slugify(content, now=now)
        inbox_dir = vault / "personal" / "_inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        target = _resolve_target(inbox_dir, resolved_slug)
        final_slug = target.stem

        fm_lines = [
            "---",
            f"kind: {kind}",
            "status: inbox",
            f"created: {_iso(now)}",
            f"captured: {_iso(now)}",
            f"slug: {final_slug}",
        ]
        if source:
            fm_lines.append(f"source: {source}")
        if surface:
            fm_lines.append(f"surface: {surface}")
        if tags:
            fm_lines.append("tags: [" + ", ".join(tags) + "]")
        if source_url:
            fm_lines.append(f"source_url: {source_url}")
        if instructions:
            fm_lines.append(f"instructions: {json.dumps(instructions)}")
        fm_lines.append("---")
        fm = "\n".join(fm_lines) + "\n"

        body = content.rstrip("\n") + "\n"
        atomic_write(target, fm + "\n" + body)
        return CaptureResult(success=True, path=target, slug=final_slug)
    except OSError as e:
        return CaptureResult(success=False, error=f"write failed: {e}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-capture",
        description=(
            "Capture a thought, link, or idea into MemoryVault's staging inbox "
            "(personal/_inbox/). Canonical Python implementation behind "
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
