#!/usr/bin/env python3
"""merge-managed-section.py — idempotently insert/replace a marker-delimited
managed section in a text/markdown file, preserving everything outside the markers.

Used by the installer to merge the AgentMemory vault-usage payload
into ~/.gemini/GEMINI.md (Antigravity's global rules file) so Antigravity picks up
the vault in every workspace without a per-project install — without clobbering the
operator's own global rules.

Markers (HTML comments — markdown-safe, invisible when rendered):

  <!-- AGENTMEMORY:BEGIN ... -->
  ...payload body...
  <!-- AGENTMEMORY:END -->

Behavior:
  - target absent            → create it containing just the managed block.
  - target present, no block → append the managed block (blank-line separated).
  - target present, has block → replace the first block IN PLACE (idempotent).
  Everything outside the markers is preserved byte-for-byte; re-running with the
  same content is a no-op ("kept").

Why python3 (not sed/awk): python3 is already a hard prereq of the installer
(install.sh checks for it), and a multiline marker-bounded replace is fiddly in
portable sed. Mirrors merge-settings-fragment.py's rationale.

Usage:
  python3 merge-managed-section.py <target> <content_file>
                                   [--marker NAME] [--strip-frontmatter]

  --marker NAME        marker token (default: AGENTMEMORY). Section is delimited
                       by `<!-- NAME:BEGIN ... -->` ... `<!-- NAME:END -->`.
  --strip-frontmatter  drop a leading YAML frontmatter block (--- ... ---) from
                       the content file before wrapping (the source is an
                       Antigravity rule whose `trigger:` frontmatter has no
                       meaning in GEMINI.md).

Exit:
  0  written (created / appended / replaced) or no-op (already current)
  1  setup error (content file missing / unreadable, target unreadable)
  2  argument error
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove a single leading YAML frontmatter block, if present."""
    return _FRONTMATTER_RE.sub("", text, count=1)


def build_block(marker: str, body: str) -> str:
    begin = (
        f"<!-- {marker}:BEGIN — managed by agentm; do not edit between these "
        f"markers (refreshed on install --update) -->"
    )
    end = f"<!-- {marker}:END -->"
    return f"{begin}\n\n{body.strip()}\n\n{end}\n"


def section_regex(marker: str) -> re.Pattern:
    return re.compile(
        re.escape(f"<!-- {marker}:BEGIN")
        + r".*?"
        + re.escape(f"<!-- {marker}:END -->")
        + r"\n?",
        re.DOTALL,
    )


def merge(existing: str, block: str, marker: str) -> tuple[str, str]:
    """Return (new_text, action). action ∈ created|appended|replaced."""
    section_re = section_regex(marker)
    if section_re.search(existing):
        # In-place replace of the first block (position-preserving + idempotent).
        # Use a function replacement so backslashes in `block` aren't treated as
        # regex backreferences.
        new_text = section_re.sub(lambda _m: block, existing, count=1)
        return new_text, "replaced"
    if existing.strip():
        if existing.endswith("\n\n"):
            sep = ""
        elif existing.endswith("\n"):
            sep = "\n"
        else:
            sep = "\n\n"
        return existing + sep + block, "appended"
    return block, "created"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="merge-managed-section.py",
        description="Idempotently insert/replace a marker-delimited managed section.",
        add_help=True,
    )
    parser.add_argument("target")
    parser.add_argument("content_file")
    parser.add_argument("--marker", default="AGENTMEMORY")
    parser.add_argument("--strip-frontmatter", action="store_true")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return 2

    content_path = Path(args.content_file)
    if not content_path.is_file():
        print(f"merge-managed-section: content not found: {content_path}", file=sys.stderr)
        return 1
    try:
        body = content_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"merge-managed-section: cannot read content: {e}", file=sys.stderr)
        return 1
    if args.strip_frontmatter:
        body = strip_frontmatter(body)

    marker = args.marker
    block = build_block(marker, body)

    target_path = Path(args.target)
    existing = ""
    if target_path.exists():
        try:
            existing = target_path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"merge-managed-section: cannot read target: {e}", file=sys.stderr)
            return 1

    new_text, action = merge(existing, block, marker)

    display = target_path.as_posix()
    if new_text == existing:
        print(f"    kept     {display} ({marker} section already current)")
        return 0

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(new_text, encoding="utf-8")
    print(f"    {action:8s} {display} ({marker} managed section)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
