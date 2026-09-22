#!/usr/bin/env python3
"""check-no-title-case-roots — no literal names a root space by its retired spelling.

The vault's root spaces are lowercase since the root casing (agentm-vault
plan 08, 2026-09-14): `agent/`, `calendar/`, `personal/` and `projects/`
beside `standards/`. The disk this vault sits on is case-insensitive, so a
stale `Agent/memory/...` still *opens* here and the local battery cannot
catch it; what breaks is every string comparison on a vault-relative path —
the ledger key, the classifier's space, the exact-name probes both stacks
already use — and Linux CI, where the same open fails. This gate is about
the comparisons, not the opens: it fails on any literal that names a root
by its Title Case spelling.

What counts as a hit, per line, comment lines skipped:

  (A) the path form — `Agent/`, `Calendar/`, `Personal/`, `Projects/` — a
      root name not glued to a longer identifier and followed by a slash;
  (B) in a code file (.py .go .sh .ps1), the bare quoted name — `"Projects"`,
      `'Calendar'` — which is how a Go constant or a Python module spells the
      directory it joins onto a vault root.

What is allowed, and reported as allowed by `--inventory`:

  - documentation and records: `wiki/`, `CHANGELOG.md`,
    `scripts/health/results/` and `scripts/health/fixtures/` (frozen
    measurements, the gold set among them — the eval corrects a moved path
    at score time, never in the fixture);
  - the finished migrations under `scripts/migrate/` and their tests: each is
    the record of a run against the layout of its day, and its `--revert`
    compares against what it recorded;
  - a line carrying the marker `root-casing:` — a map's heading that is not
    a path, a frozen remap's old side, the contract block's three space
    lines that plan 11 rewrites, a test that builds the old layout on purpose.
    The marker is a comment that says why, where the literal sits, rather
    than a second allowlist of line numbers that drifts.

  --inventory   print every hit, allowed ones marked, with a count: the
                repoint inventory the plan applies. Exit 0.
  (default)     print the unallowed hits and exit 1 on any; 0 when clean.

Exit: 0 clean · 1 violations · 2 setup error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOTS = ("Agent", "Calendar", "Personal", "Projects")
MARKER = "root-casing:"

# (A) the path form: a root name not glued to an identifier and followed by `/`,
# or closing a path — after a `/` and before a quote, a space, a bracket or
# the end (`"../Projects"`, `<vault>/Agent`); `~/Projects` is the operator's
# home, not the vault's.
_PATH_FORM = re.compile(r"(?<![A-Za-z0-9_-])(?:%s)/|(?<!~)/(?:%s)(?=[\"'\s)\],`]|$)"
                        % ("|".join(ROOTS), "|".join(ROOTS)))
# (B) the bare quoted name, in code files only.
_BARE_FORM = re.compile(r"""(?<![A-Za-z0-9_])["'](?:%s)["'](?![A-Za-z0-9_])""" % "|".join(ROOTS))
_CODE_SUFFIXES = frozenset({".py", ".go", ".sh", ".ps1"})
_EXTENSIONS = frozenset({".py", ".go", ".sh", ".ps1", ".md", ".txt", ".yml", ".yaml", ".json", ".toml"})
_COMMENT_STARTS = ("#", "//", "/*", "*", "--", "<!--", ";")

_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".harness", ".claude"})
# Repo-relative prefixes that are documentation or frozen records.
_ALLOWED_PREFIXES = (
    "wiki/",
    "scripts/health/results/",
    "scripts/health/fixtures/",
)
_FINISHED_MIGRATIONS = (
    "scripts/migrate/card_backfill.py",
    "scripts/migrate/corpus_migration_3.py",
    "scripts/migrate/maps_and_root_notes.py",
    "scripts/migrate/memory_root_trims.py",
    "scripts/migrate/projects_merge_2b.sh",
    "scripts/migrate/purge_tool_stubs.py",
    "scripts/migrate/structural_2a.sh",
    "scripts/test_card_backfill.py",
    "scripts/test_corpus_migration_3.py",
    "scripts/test_maps_and_root_notes_migration.py",
    "scripts/test_memory_root_trims_migration.py",
    "scripts/test_projects_merge_migration.py",
    "scripts/test_purge_tool_stubs.py",
    "scripts/test_structural_2a_migration.py",
)
_ALLOWED_FILES = {
    "CHANGELOG.md": "documentation",
    # The September payload, kept so the layout-free gate can be shown to fail.
    "scripts/fixtures/agentmemory-context.september.md": "frozen records",
    # The finished migrations, and their tests, keep the spellings of their day.
    **{rel: "a finished migration" for rel in _FINISHED_MIGRATIONS},
}
# The gate names the patterns it looks for, and so does its test.
_SKIP_NAMES = frozenset({Path(__file__).name, "test_check_no_title_case_roots.py"})


def _walk(root: Path):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            p = Path(dirpath) / name
            if name in _SKIP_NAMES or p.suffix not in _EXTENSIONS:
                continue
            yield p


def _allowed_by_place(rel: str) -> str | None:
    """Why a file is allowed as a whole, or None."""
    if rel in _ALLOWED_FILES:
        return _ALLOWED_FILES[rel]
    for prefix in _ALLOWED_PREFIXES:
        if rel.startswith(prefix):
            return "frozen records" if prefix.startswith("scripts/health") else "documentation"
    return None


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(_COMMENT_STARTS)


def scan_file(path: Path, rel: str) -> list[dict]:
    """Every hit in one file: `{rel, line, form, text, allowed}`."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    place = _allowed_by_place(rel)
    code = path.suffix in _CODE_SUFFIXES
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_comment(line):
            continue
        form = None
        if _PATH_FORM.search(line):
            form = "path"
        elif code and _BARE_FORM.search(line):
            form = "bare-name"
        if form is None:
            continue
        allowed = place if place else ("marker" if MARKER in line else None)
        out.append({"rel": rel, "line": lineno, "form": form, "text": line.strip()[:120], "allowed": allowed})
    return out


def scan(root: Path) -> list[dict]:
    hits: list[dict] = []
    for p in _walk(root):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            rel = p.as_posix()
        hits.extend(scan_file(p, rel))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="repo root to scan (default: the script's own repo)")
    ap.add_argument("--inventory", action="store_true",
                    help="print every hit, allowed ones marked, with a count; exit 0")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"check-no-title-case-roots: not a directory: {root}", file=sys.stderr)
        return 2

    hits = scan(root)
    open_hits = [h for h in hits if not h["allowed"]]
    if args.inventory:
        for h in hits:
            tag = f"  (allowed: {h['allowed']})" if h["allowed"] else ""
            print(f"  {h['rel']}:{h['line']} [{h['form']}]  {h['text']}{tag}")
        files = len({h["rel"] for h in open_hits})
        allowed = len(hits) - len(open_hits)
        print(f"check-no-title-case-roots: inventory — {len(open_hits)} literal(s) to repoint in {files} file(s); "
              f"{allowed} allowed")
        return 0

    if not open_hits:
        print(f"check-no-title-case-roots: clean ({len(hits) - len(open_hits)} allowed literal(s))")
        return 0
    print(f"check-no-title-case-roots: {len(open_hits)} literal(s) name a root space by its retired "
          "Title Case spelling — the roots are `agent/`, `calendar/`, `personal/`, `projects/`.",
          file=sys.stderr)
    print(f"  Lowercase the literal, or explain it where it sits with `{MARKER} <why>` "
          "(a heading that is not a path, a frozen record's old side).", file=sys.stderr)
    for h in open_hits:
        print(f"  {h['rel']}:{h['line']} [{h['form']}]  {h['text']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
