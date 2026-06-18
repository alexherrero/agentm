#!/usr/bin/env python3
# ideas_surface.py — Tier-1 idea writer (Ideas.md surface-tier).
#
# Appends idea-candidate entries to ~/Obsidian/Ideas.md (single file at the
# user's vault root, append-only). This is the SURFACE tier of the two-tier
# idea-capture system per locked design call B1.i; the DEEP-research tier
# (_idea-incubator/<slug>/) lands in task 3 of this part.
#
# Section format (locked in parent design):
#
#   ## YYYY-MM-DD: <Idea Title>
#   <2-sentence summary>
#   See deep research: [[MemoryVault/personal/_idea-incubator/<slug>/_index.md]]
#
# Sections sorted by date-prefix; new ideas append to the bottom (file is
# read-only-mostly for the user; the agent appends without touching
# pre-existing content).
#
# The Ideas.md file lives OUTSIDE MemoryVault/ (at ~/Obsidian/Ideas.md by
# default). Per the A3 permeable-write-boundary locked design call, every
# write requires confirmation via permeable_boundary.confirm_write_outside_
# memoryvault(). Reflection-driven writes route through the agent-initiated
# + user-confirmed path; direct-user-invocation (future /memory idea
# command) routes through the explicit-user-request path (still goes
# through this helper but caller may set mode='silent' if user-initiated).
#
# Plan #7a part 4 task 2 (this commit) ships ONLY the surface writer +
# permeable boundary integration. Task 3 wires this from the reflection
# sidecar; task 4 handles the promotion + GC; task 5 documents the full
# flow in Use-The-Memory-Skill.md.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Lazy import so callers using only the silent-mode path don't pay the
# import cost. sys.path injection follows the same pattern as reflect.py.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# Default Ideas.md path: ~/Obsidian/Ideas.md per parent design + B1.i.
# Operators using a different Obsidian vault root override via the
# IDEAS_SURFACE_PATH env var or pass --ideas-path to the CLI.
def _resolve_ideas_path(arg_path: str | None) -> Path:
    """Resolve Ideas.md path: arg → env → default ~/Obsidian/Ideas.md."""
    if arg_path:
        return Path(arg_path).expanduser()
    env_path = os.environ.get("IDEAS_SURFACE_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / "Obsidian" / "Ideas.md"


def _slugify_title(title: str, max_len: int = 40) -> str:
    """Generate a kebab-case slug from a title.

    Lowercases, extracts alphanumeric runs, joins with '-', truncates to
    max_len chars. Returns "untitled-idea" if input yields no alphanumerics.
    Slug collision handling (-2, -3, ... suffix) is the caller's job —
    typically done at incubator-dir creation time (task 3).
    """
    words = re.findall(r"[a-z0-9]+", title.lower())
    slug = "-".join(words)
    if not slug:
        return "untitled-idea"
    return slug[:max_len].rstrip("-")


def _validate_summary(summary: str) -> str:
    """Normalize + lightly validate the idea summary.

    Strips leading/trailing whitespace, collapses runs of newlines to
    single spaces (the section body is 1-2 sentences — no embedded
    blank lines). Returns the cleaned summary.

    Raises ValueError if summary is empty after cleaning.
    """
    cleaned = re.sub(r"\s+", " ", summary).strip()
    if not cleaned:
        raise ValueError("idea summary must be non-empty after whitespace strip")
    return cleaned


def _format_section(
    title: str, summary: str, slug: str, *, date_iso: str
) -> str:
    """Build the locked Ideas.md section format."""
    return (
        f"## {date_iso}: {title}\n"
        f"{summary}\n"
        f"See deep research: [[MemoryVault/personal/_idea-incubator/{slug}/_index.md]]\n"
    )


def append_idea_to_surface(
    title: str,
    summary: str,
    incubator_slug: str | None = None,
    *,
    ideas_path: Path | str | None = None,
    surfaced_at: datetime | None = None,
    mode: str | None = None,
    stdin=None,
    stdout=None,
) -> Path | None:
    """Append an idea section to ~/Obsidian/Ideas.md (or override path).

    Args:
        title: idea title (used in the section header).
        summary: 1-2 sentence pitch (the section body; multi-line input
            gets collapsed to a single line per the locked format).
        incubator_slug: kebab-case slug for the wikilink target. If None,
            derived from `title` via `_slugify_title`. Caller (task 3 —
            incubator writer) typically passes the slug it already
            generated for the directory; passing it here keeps the
            surface + deep-tier in lockstep.
        ideas_path: override Ideas.md location. Resolves arg → env
            (IDEAS_SURFACE_PATH) → default ~/Obsidian/Ideas.md.
        surfaced_at: timestamp for the section header (defaults to
            today UTC).
        mode: routes through `permeable_boundary.confirm_write_outside_
            memoryvault()` — 'silent' / 'interactive' / 'auto' / None
            (default 'interactive' via env / module default).
        stdin, stdout: passed to the boundary helper for prompt I/O.

    Returns:
        Path written on success; None if the operator denied the
        cross-boundary write (or if the boundary helper returned False
        for any reason, including non-TTY auto-mode safety).

    Raises:
        ValueError: if title or summary are empty.
    """
    from permeable_boundary import confirm_write_outside_memoryvault  # lazy import

    title = (title or "").strip()
    if not title:
        raise ValueError("idea title must be non-empty")
    summary = _validate_summary(summary)
    slug = incubator_slug or _slugify_title(title)

    target = _resolve_ideas_path(str(ideas_path) if ideas_path else None)

    if surfaced_at is None:
        surfaced_at = datetime.now(timezone.utc)
    date_iso = surfaced_at.strftime("%Y-%m-%d")

    section = _format_section(title, summary, slug, date_iso=date_iso)

    # A3 permeable-boundary check. Compose a clear rationale + the section
    # we'd append as the content preview.
    rationale = (
        f"Reflection-sidecar surfaced idea {slug!r}. Append section to "
        f"{target} (single-file, append-only) for surface-tier discovery."
    )
    approved = confirm_write_outside_memoryvault(
        target_path=target,
        content_preview=section,
        rationale=rationale,
        mode=mode,
        stdin=stdin,
        stdout=stdout,
    )
    if not approved:
        return None

    # Create parent dirs if missing (operator's first idea ever).
    target.parent.mkdir(parents=True, exist_ok=True)

    # If file doesn't exist, write a small header before the section so
    # the operator's first read of the file is friendly. Subsequent appends
    # don't touch this header.
    if not target.exists():
        header = (
            "# Ideas\n"
            "\n"
            "Surface-tier idea ledger written by the MemoryVault reflection "
            "sidecar (plan #7a part 4). Each section is a 2-sentence pitch "
            "linking to a deep-research entry under "
            "`MemoryVault/personal/_idea-incubator/<slug>/`.\n"
            "\n"
            "Append-only — sections accumulate over time, sorted by surfaced "
            "date. Promotion graduates an idea to "
            "`MemoryVault/projects/<slug>/` via "
            "`/memory promote idea <slug>` (annotation appended to the "
            "section). GC at 6 months without engagement (confirmed; never "
            "silent deletion).\n"
            "\n"
        )
        target.write_bytes(header.encode("utf-8"))

    # Append the section. Ensure a blank line between sections (the existing
    # file may or may not end with a trailing newline; we add one before
    # the new section to guarantee separation).
    existing = target.read_bytes()
    needs_leading_newline = existing and not existing.endswith(b"\n\n")
    sep = b"\n" if needs_leading_newline else b""
    with open(target, "ab") as f:
        f.write(sep + section.encode("utf-8") + b"\n")

    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-ideas-surface",
        description=(
            "Append an idea section to ~/Obsidian/Ideas.md (the surface-tier "
            "of the MemoryVault idea ledger). Tier-1 writer; tier-2 deep-"
            "research lands in plan #7a part 4 task 3. Routes through the "
            "A3 permeable-write-boundary helper — set --mode silent or "
            "MEMORY_REVIEW_MODE=silent for non-interactive contexts; "
            "default mode prompts via TTY (denies if no TTY)."
        ),
    )
    parser.add_argument("title", help="idea title (used in section header)")
    parser.add_argument("summary", help="1-2 sentence pitch (section body)")
    parser.add_argument(
        "--slug", default=None,
        help="kebab-case slug for the wikilink target (default: derived from title)",
    )
    parser.add_argument(
        "--ideas-path", default=None,
        help="override Ideas.md path (default: $IDEAS_SURFACE_PATH or ~/Obsidian/Ideas.md)",
    )
    parser.add_argument(
        "--mode", choices=["silent", "interactive", "auto"], default=None,
        help="permeable-boundary mode override (default: $MEMORY_REVIEW_MODE or interactive)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = append_idea_to_surface(
            title=args.title,
            summary=args.summary,
            incubator_slug=args.slug,
            ideas_path=args.ideas_path,
            mode=args.mode,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if result is None:
        print(json.dumps({"appended": False, "reason": "permeable_boundary denied"}))
        return 2  # Distinct exit code for boundary-denial (callers can branch).
    print(json.dumps({"appended": True, "path": str(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
