#!/usr/bin/env python3
"""check-sync-artifacts — the Drive `Icon` rule has one home.

Google Drive writes a file named `Icon` plus a carriage return into every
folder it mirrors, and Finder leaves `.DS_Store` beside it. Neither is content,
and a directory holding only these is empty in every sense a caller means — but
they are files, so a walk that counts entries finds something.

The rule for skipping them spread as `p.name.startswith("Icon")`, hand-copied
into ten call sites over several months, each added by whoever next tripped
over it. That is the failure this gate exists for: a copied idiom is not
inherited by the walker somebody writes next week, and the one that missed it
left five directories standing after the corpus migration's cleanup.

So the predicate lives in `drive_artifacts` (Python) and `note.SyncArtifact`
(Go), and this gate fails on a fresh copy of the literal anywhere else. It is a
grep, deliberately: the thing being prevented is a string being retyped, and a
grep is exactly the shape of that problem.

Usage:
    check-sync-artifacts.py [--root <repo>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The homes. These files are allowed to name the artifacts, because naming them
# is their job.
ALLOWED = {
    "harness/skills/memory/scripts/drive_artifacts.py",
    "daemon/internal/note/space.go",
    "scripts/check-sync-artifacts.py",
}

# Tests may write the literal: a fixture that plants a real `Icon\r` is exactly
# what proves the predicate works, and it has to spell the name to create it.
ALLOWED_PATTERNS = (
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.go$"),
)

SEARCHED_SUFFIXES = {".py", ".go", ".sh", ".ps1"}
SKIPPED_DIRS = {".git", ".claude", "node_modules", "__pycache__", ".venv", "dist"}

# A hand-rolled test for the artifact names. Deliberately narrow: it looks for
# the *predicate*, not for the word — a comment or a docstring explaining the
# rule is not a second copy of it.
OFFENDERS = (
    (re.compile(r"""\.startswith\(\s*["']Icon["']"""),
     'a hand-rolled "Icon" prefix test'),
    (re.compile(r"""HasPrefix\([^,]+,\s*"Icon"\s*\)"""),
     'a hand-rolled "Icon" prefix test'),
    (re.compile(r"""(==|!=)\s*["']\.DS_Store["']"""),
     'a hand-rolled .DS_Store comparison'),
    (re.compile(r"""glob\(\s*["']Icon\*["']\s*\)"""),
     'a hand-rolled Icon glob'),
)


def offending_lines(text: str):
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        for pattern, why in OFFENDERS:
            if pattern.search(line):
                yield lineno, why, stripped
                break


def allowed(rel: str) -> bool:
    if rel in ALLOWED:
        return True
    return any(p.search(rel) for p in ALLOWED_PATTERNS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SEARCHED_SUFFIXES:
            continue
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if allowed(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, why, line in offending_lines(text):
            findings.append((rel, lineno, why, line))

    if not findings:
        print("check-sync-artifacts: clean — the Drive artifact rule has one home")
        return 0

    print("check-sync-artifacts: FAIL — the sync-artifact rule was re-copied\n",
          file=sys.stderr)
    for rel, lineno, why, line in findings:
        print(f"  {rel}:{lineno}: {why}", file=sys.stderr)
        print(f"    {line}", file=sys.stderr)
    print("\nUse the shared predicate instead:", file=sys.stderr)
    print("  Python: `import drive_artifacts` -> `drive_artifacts.is_artifact(p)`,",
          file=sys.stderr)
    print("          `drive_artifacts.visible(d.iterdir())`, "
          "`drive_artifacts.is_empty(d)`", file=sys.stderr)
    print("  Go:     `note.SyncArtifact(d.Name())`", file=sys.stderr)
    print("\nA copied idiom is not inherited by the next walker somebody writes; "
          "that is\nhow five directories survived the corpus migration's cleanup.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
