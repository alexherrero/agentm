#!/usr/bin/env python3
# ideas_incubator.py — Tier-2 deep-research writer for the idea ledger.
#
# Creates the `<vault>/personal-private/_idea-incubator/<slug>/` directory
# with the locked file set per the parent design (B1.i):
#
#   <slug>/
#     _index.md                   # frontmatter + agent reasoning + cross-refs
#     research-pending.md         # placeholder for memory-idea-researcher sub-agent
#     related-memoryvault.md      # placeholder for cross-ref scan
#     related-obsidian.md         # placeholder for Obsidian-notes scan
#
# This writer ships the SKELETON. The actual deep research (web fetches +
# cross-ref against existing MemoryVault entries + scan of Obsidian notes)
# is performed by the `memory-idea-researcher` sub-agent (shipped alongside
# this script in plan #7a part 4 task 3). The sub-agent reads the skeleton's
# _index.md for context + fills the research-*.md / related-*.md placeholders.
#
# Operator-facing alternative: invoke `ideas_incubator.py` directly to set
# up the skeleton, then either dispatch the sub-agent or fill the placeholder
# files by hand. The skeleton is functional without the sub-agent (operator
# triage path).
#
# The incubator dir lives INSIDE MemoryVault/, so no permeable-boundary
# confirmation is needed (in contrast to ideas_surface.py which writes to
# ~/Obsidian/Ideas.md outside the vault root).
#
# Plan #7a part 4 task 3 (this commit) ships:
#   1. This Python skeleton writer.
#   2. agents/memory-idea-researcher.md sub-agent body (deep-research worker).
# Task 4 wires `/memory promote idea` + GC; task 5 documents the full flow.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Default deep-research budget caps per locked design call B1.i. Operators
# wanting tighter / looser limits override via env vars at sub-agent
# dispatch time. The Python writer doesn't enforce these — it just records
# them in the _index.md so the sub-agent reads them.
DEFAULT_BUDGET_WALL_TIME_SEC = 300   # 5 minutes
DEFAULT_BUDGET_WEB_FETCHES = 3
DEFAULT_BUDGET_TOKENS = 5000


def _resolve_vault_path(arg: str | None) -> Path:
    """Resolve vault path: arg → MEMORY_VAULT_PATH env → error."""
    if arg:
        return Path(arg).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or MEMORY_VAULT_PATH env var."
    )


def _slugify(value: str, max_len: int = 40) -> str:
    """Generate a kebab-case slug from a title; truncate at max_len."""
    words = re.findall(r"[a-z0-9]+", value.lower())
    slug = "-".join(words)[:max_len].rstrip("-")
    return slug or "untitled-idea"


def _next_available_slug(base_dir: Path, slug: str) -> str:
    """Resolve slug collisions: <slug>, <slug>-2, <slug>-3, ..."""
    target = base_dir / slug
    if not target.exists():
        return slug
    n = 2
    while (base_dir / f"{slug}-{n}").exists():
        n += 1
    return f"{slug}-{n}"


def _build_index_md(
    title: str,
    summary: str,
    slug: str,
    *,
    session_id: str | None,
    surfaced_at: datetime,
    rationale: str | None,
    excerpts: list[str] | None,
    budget_wall_time_sec: int,
    budget_web_fetches: int,
    budget_tokens: int,
) -> str:
    """Compose the _index.md content for a freshly-incubated idea.

    Frontmatter follows the locked schema (kind / status / surfaced_in_session
    / surfaced_at + the agent's reasoning + cross-refs + budget). Body is
    structured for the memory-idea-researcher sub-agent to read at dispatch.
    """
    surfaced_iso = surfaced_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    surfaced_date = surfaced_at.strftime("%Y-%m-%d")

    fm_lines = [
        "---",
        "kind: idea",
        "status: incubating",
        f"slug: {slug}",
        f"surfaced_at: {surfaced_iso}",
    ]
    if session_id:
        fm_lines.append(f"surfaced_in_session: {session_id}")
    fm_lines.extend([
        f"created: {surfaced_date}",
        f"updated: {surfaced_date}",
        f"research_budget_wall_time_sec: {budget_wall_time_sec}",
        f"research_budget_web_fetches: {budget_web_fetches}",
        f"research_budget_tokens: {budget_tokens}",
        "---",
        "",
    ])
    frontmatter = "\n".join(fm_lines)

    body = (
        f"# {title}\n"
        f"\n"
        f"## Surface summary\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"## Agent reasoning\n"
        f"\n"
    )
    if rationale:
        body += f"**Why this surfaced**: {rationale}\n\n"
    if excerpts:
        body += "**Supporting excerpts from session**:\n"
        for ex in excerpts:
            body += f"\n> {ex}\n"
        body += "\n"

    body += (
        f"## Deep-research status\n"
        f"\n"
        f"Skeleton created {surfaced_iso}. The `memory-idea-researcher` "
        f"sub-agent has not yet been dispatched against this entry — the "
        f"placeholder files (`research-pending.md`, `related-memoryvault.md`, "
        f"`related-obsidian.md`) are stubs. Dispatch the sub-agent to fill "
        f"them, or fill manually for operator-driven triage.\n"
        f"\n"
        f"### Research budget (caps; sub-agent enforces)\n"
        f"\n"
        f"- Wall-time: **{budget_wall_time_sec}s**\n"
        f"- Web fetches: **{budget_web_fetches}**\n"
        f"- Tokens: **{budget_tokens}**\n"
        f"\n"
        f"Budget overrun produces partial results + a flag in this file's "
        f"frontmatter (`research_status: partial`); never blocks the "
        f"calling session.\n"
        f"\n"
        f"## Promotion + GC\n"
        f"\n"
        f"- **Promote** to a real project: `/memory promote idea {slug}` "
        f"moves this dir to `projects/{slug}/` + appends "
        f"`→ promoted YYYY-MM-DD` annotation to the corresponding "
        f"`Ideas.md` section.\n"
        f"- **GC**: this entry is eligible for GC review at 6 months "
        f"without engagement (configurable via `memory.incubator_gc_months`). "
        f"GC presents Keep / Archive / Delete options — never silent.\n"
    )

    return frontmatter + body


_PLACEHOLDER_RESEARCH = """# Deep research — pending

This file is a placeholder. Dispatch the `memory-idea-researcher` sub-agent
to fill it with web-fetch summaries + source links. Alternatively, the
operator can fill manually as part of triage.

When the sub-agent runs, it replaces this file with one or more
`research-<source-slug>.md` files — one per web fetch up to the budget cap.
The sub-agent does NOT modify `_index.md` body content; it only updates
`_index.md` frontmatter's `research_status` field (`pending` → `complete`
or `partial`).
"""

_PLACEHOLDER_MEMORYVAULT = """# Related MemoryVault entries

Placeholder for cross-reference scan results. The `memory-idea-researcher`
sub-agent invokes the recall engine against the idea's title + summary +
deep-research-discovered keywords to find existing related entries.

Format when filled:

    ## <existing-slug> (kind: <kind>, sim=<score>)
    Path: <vault-relative path>
    Why related: <1-2 sentences>

Operator triage path: prune false positives + promote relevant cross-refs
to the `_index.md`'s body before `/memory promote idea`.
"""

_PLACEHOLDER_OBSIDIAN = """# Related Obsidian notes (outside MemoryVault)

Placeholder for scan results against the user's existing Obsidian notes
outside `MemoryVault/`. The `memory-idea-researcher` sub-agent scans the
Obsidian vault root (excluding the `MemoryVault/` subtree to avoid
overlap with the previous file) for notes matching the idea's keywords.

This is READ-ONLY surface — the sub-agent never writes to Obsidian notes.
Found notes are listed here for the operator's awareness; promotion may
involve manually moving Obsidian notes into the new project dir.

Format when filled:

    ## <note-filename>
    Path: <absolute path under ~/Obsidian/>
    Match: <keywords or excerpts>
    Last modified: <iso-date>
"""


def create_incubator_skeleton(
    title: str,
    summary: str,
    *,
    vault_path: Path | str | None = None,
    slug: str | None = None,
    session_id: str | None = None,
    surfaced_at: datetime | None = None,
    rationale: str | None = None,
    excerpts: list[str] | None = None,
    budget_wall_time_sec: int = DEFAULT_BUDGET_WALL_TIME_SEC,
    budget_web_fetches: int = DEFAULT_BUDGET_WEB_FETCHES,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> Path:
    """Create the `_idea-incubator/<slug>/` directory + 4 skeleton files.

    Returns the absolute path of the created directory.

    Raises:
        FileNotFoundError: if vault_path doesn't resolve (caller didn't pass
            arg + MEMORY_VAULT_PATH env unset).
        ValueError: if title or summary are empty after strip.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("idea title must be non-empty")
    summary = re.sub(r"\s+", " ", summary or "").strip()
    if not summary:
        raise ValueError("idea summary must be non-empty after whitespace strip")

    vault = _resolve_vault_path(str(vault_path) if vault_path else None)
    if not vault.exists():
        raise FileNotFoundError(f"vault path does not exist: {vault}")

    if not slug:
        slug = _slugify(title)

    incubator_root = vault / "personal-private" / "_idea-incubator"
    incubator_root.mkdir(parents=True, exist_ok=True)

    # Collision suffix (<slug>, <slug>-2, ...) — safe since slug-collision
    # for genuinely-new ideas is rare; sub-agent's deep research may surface
    # near-duplicates which the operator handles via triage.
    final_slug = _next_available_slug(incubator_root, slug)
    target_dir = incubator_root / final_slug
    target_dir.mkdir(parents=True)

    if surfaced_at is None:
        surfaced_at = datetime.now(timezone.utc)

    index_content = _build_index_md(
        title=title, summary=summary, slug=final_slug,
        session_id=session_id, surfaced_at=surfaced_at,
        rationale=rationale, excerpts=excerpts,
        budget_wall_time_sec=budget_wall_time_sec,
        budget_web_fetches=budget_web_fetches,
        budget_tokens=budget_tokens,
    )
    (target_dir / "_index.md").write_bytes(index_content.encode("utf-8"))
    (target_dir / "research-pending.md").write_bytes(_PLACEHOLDER_RESEARCH.encode("utf-8"))
    (target_dir / "related-memoryvault.md").write_bytes(_PLACEHOLDER_MEMORYVAULT.encode("utf-8"))
    (target_dir / "related-obsidian.md").write_bytes(_PLACEHOLDER_OBSIDIAN.encode("utf-8"))

    return target_dir


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-ideas-incubator",
        description=(
            "Create the `_idea-incubator/<slug>/` skeleton for a freshly-"
            "surfaced idea candidate. Tier-2 of the two-tier idea-capture; "
            "complements ideas_surface.py (tier-1). Sub-agent "
            "memory-idea-researcher fills the research-*.md / related-*.md "
            "placeholders. Plan #7a part 4 task 3."
        ),
    )
    parser.add_argument("title", help="idea title")
    parser.add_argument("summary", help="1-2 sentence pitch")
    parser.add_argument(
        "--vault-path", default=None,
        help="MemoryVault root (default: $MEMORY_VAULT_PATH)",
    )
    parser.add_argument("--slug", default=None,
                        help="kebab-case slug (default: derived from title)")
    parser.add_argument("--session-id", default=None,
                        help="Claude Code session UUID (for surfaced_in_session frontmatter)")
    parser.add_argument("--rationale", default=None,
                        help="why this idea surfaced (from reflection sidecar)")
    parser.add_argument(
        "--excerpt", action="append", default=None, metavar="TEXT",
        help="supporting transcript excerpt (repeatable)",
    )
    parser.add_argument("--budget-wall-time-sec", type=int,
                        default=DEFAULT_BUDGET_WALL_TIME_SEC,
                        help=f"deep-research wall-time cap (default {DEFAULT_BUDGET_WALL_TIME_SEC})")
    parser.add_argument("--budget-web-fetches", type=int,
                        default=DEFAULT_BUDGET_WEB_FETCHES,
                        help=f"deep-research web-fetch cap (default {DEFAULT_BUDGET_WEB_FETCHES})")
    parser.add_argument("--budget-tokens", type=int,
                        default=DEFAULT_BUDGET_TOKENS,
                        help=f"deep-research token cap (default {DEFAULT_BUDGET_TOKENS})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        target = create_incubator_skeleton(
            title=args.title,
            summary=args.summary,
            vault_path=args.vault_path,
            slug=args.slug,
            session_id=args.session_id,
            rationale=args.rationale,
            excerpts=args.excerpt,
            budget_wall_time_sec=args.budget_wall_time_sec,
            budget_web_fetches=args.budget_web_fetches,
            budget_tokens=args.budget_tokens,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(json.dumps({
        "created": True,
        "incubator_dir": str(target),
        "slug": target.name,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
