#!/usr/bin/env python3
"""check-no-harness-paths — no code composes, probes or names `_harness/`.

A project's state root is the project directory itself since the projects
migration (agentm-vault plan 10, 2026-09-16): tasks under `tasks/NNN-<slug>/`,
machine files under `desk/`, designs under `designs/`, `followups.md` and
`roadmap.md` at the root. Plan 15 retired every fallback that still reached for
the vault's `_harness/`. This gate keeps the word out of the code, because the
behaviour cannot be caught any other way: a reader that composes `_harness/` and
tests `is_dir()` fails soft on a vault where the directory is gone, so nothing
breaks and nothing says so. The doctor's `harness-dirs` row is the other half —
it catches a directory that came back on disk.

What counts as a hit: the literal `_harness` not glued to a longer identifier
(`resolve_harness_root` and `test_harness_memory.py` are not hits), on any line,
comments included. A comment naming `_harness/` as a place teaches the next
reader a place that no longer exists.

Scanned: the repo's tracked files (`git ls-files`; a directory walk when the
root is not a git work tree) with a code or text suffix. Test files are not
scanned — a test may build the old layout on purpose, to prove it is refused.

What is allowed, and reported as allowed by `--inventory`:

  - documentation and frozen records: `wiki/`, `CHANGELOG.md`,
    `scripts/health/results/` and `scripts/health/fixtures/` (the gold set
    among them — the eval translates a moved path at score time, never in the
    fixture);
  - a line carrying the marker `harness-deprecation:` with the reason where the
    literal sits: an eval remap row's old side, a gate's list of words it
    refuses;
  - a whole file whose opening lines carry `harness-deprecation: file` — a
    finished migration, which records the layout of its day and whose revert
    compares against what it recorded.

The repo-local `.harness/` (`init.sh`, `verify.sh`, `project.json` …) is a
different directory and is never a hit.

  --inventory   print every hit, allowed ones marked, with a count. Exit 0.
  (default)     print the unallowed hits and exit 1 on any; 0 when clean.

Exit: 0 clean · 1 violations · 2 setup error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

MARKER = "harness-deprecation:"
FILE_MARKER = "harness-deprecation: file"
# How far into a file the file-scope marker may sit: a module docstring's
# opening, not a line buried in the body.
_FILE_MARKER_LINES = 40

# `_harness` as a name of its own: not the tail of an identifier on the left
# (`resolve_harness`), nor the head of one on the right (`_harness_dir`).
_LITERAL = re.compile(r"(?<![A-Za-z0-9])_harness(?![A-Za-z0-9_])")
_EXTENSIONS = frozenset({".py", ".go", ".sh", ".ps1", ".md", ".txt", ".yml", ".yaml",
                         ".json", ".toml"})
_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".harness", ".claude"})
# Repo-relative prefixes that are documentation or frozen records.
_ALLOWED_PREFIXES = {
    "wiki/": "documentation",
    "scripts/health/results/": "frozen records",
    "scripts/health/fixtures/": "frozen records",
}
_ALLOWED_FILES = {"CHANGELOG.md": "documentation"}
# The gate names the pattern it looks for, and so does its test.
_SKIP_NAMES = frozenset({Path(__file__).name, "test_check_no_harness_paths.py"})


def _is_test(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    parts = rel.split("/")
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.go")
        or name == "conftest.py"
        or "testdata" in parts[:-1]
    )


def _tracked(root: Path) -> list[str] | None:
    """Repo-relative paths git tracks under `root`, or None off a work tree."""
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return sorted(p for p in r.stdout.decode("utf-8", "replace").split("\0") if p)


def _walked(root: Path) -> list[str]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in files:
            out.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(out)


def _candidates(root: Path) -> list[str]:
    rels = _tracked(root)
    if rels is None:
        rels = _walked(root)
    return [
        rel for rel in rels
        if Path(rel).suffix in _EXTENSIONS
        and rel.rsplit("/", 1)[-1] not in _SKIP_NAMES
        and not any(part in _SKIP_DIRS for part in rel.split("/")[:-1])
        and not _is_test(rel)
    ]


def _allowed_by_place(rel: str) -> str | None:
    if rel in _ALLOWED_FILES:
        return _ALLOWED_FILES[rel]
    for prefix, why in _ALLOWED_PREFIXES.items():
        if rel.startswith(prefix):
            return why
    return None


def scan_file(path: Path, rel: str) -> list[dict]:
    """Every hit in one file: `{rel, line, text, allowed}`."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    place = _allowed_by_place(rel)
    if place is None and any(FILE_MARKER in line for line in lines[:_FILE_MARKER_LINES]):
        place = "marker (file)"
    out = []
    for lineno, line in enumerate(lines, 1):
        if not _LITERAL.search(line):
            continue
        allowed = place if place else ("marker" if MARKER in line else None)
        out.append({"rel": rel, "line": lineno, "text": line.strip()[:120], "allowed": allowed})
    return out


def scan(root: Path) -> list[dict]:
    hits: list[dict] = []
    for rel in _candidates(root):
        p = root / rel
        if p.is_file():
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
        print(f"check-no-harness-paths: not a directory: {root}", file=sys.stderr)
        return 2

    hits = scan(root)
    open_hits = [h for h in hits if not h["allowed"]]
    files = len({h["rel"] for h in open_hits})
    allowed = len(hits) - len(open_hits)
    if args.inventory:
        for h in hits:
            tag = f"  (allowed: {h['allowed']})" if h["allowed"] else ""
            print(f"  {h['rel']}:{h['line']}  {h['text']}{tag}")
        print(f"check-no-harness-paths: inventory — {len(open_hits)} literal(s) to retire in "
              f"{files} file(s); {allowed} allowed in "
              f"{len({h['rel'] for h in hits if h['allowed']})} file(s)")
        return 0

    if not open_hits:
        print(f"check-no-harness-paths: clean ({allowed} allowed literal(s))")
        return 0
    print(f"check-no-harness-paths: {len(open_hits)} literal(s) in {files} file(s) name `_harness` — "
          "a project's state root is its own directory: `tasks/`, `desk/`, `designs/`.",
          file=sys.stderr)
    print(f"  Resolve the path from the project skeleton, or explain the literal where it sits "
          f"with `{MARKER} <why>` (a frozen remap's old side, a word a gate refuses).",
          file=sys.stderr)
    for h in open_hits:
        print(f"  {h['rel']}:{h['line']}  {h['text']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
