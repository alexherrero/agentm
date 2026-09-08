#!/usr/bin/env python3
"""check-payload-layout-free.py — the payload template names no folder a
migration moves.

The pasted payload has been wrong on three chat surfaces since July for one
reason: it carried a folder map, and the folder map is the thing every landing
group changes. The agentm-vault design's answer is a layout-free payload that
names three things that do not move — `index.md`, `standards/` and
`moc-projects.md` — and lets the map explain itself. This gate is the tripwire
that keeps it that way: it fails when the template re-acquires any of the eight
names the vault series retires, or the retired unfiled marker.

The scan is CASE-SENSITIVE on purpose. `Agent/` is the capitalized root space
the casing rename retires; `agent/archive/` is the lowercase path the card
guide is *required* to explain, and the two differ only by that letter.

Usage:  python3 scripts/check-payload-layout-free.py [<template path>]
Exit:   0 iff the template carries none of the forbidden names.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = REPO / "templates" / "agentmemory-context.md"

# The design's own list, in its order. Each entry is (needle, why it is out).
FORBIDDEN = [
    ("Agent/", "the capitalized root space; the casing rename retires it"),
    ("Projects/", "the capitalized projects space; it becomes projects/"),
    ("_inbox", "there is no inbox; the metadata is the inbox"),
    ("_always-load", "the always-load pen folds into standards/"),
    ("_index.md", "the project anchor becomes tracker.md / charter.md"),
    ("_harness", "harness state is not something a chat surface reads"),
    ("Agent/desk", "the desk moves out of the capitalized root"),
    ("Agent/_meta", "the machine directory and the payload twin retire"),
    ("filing_confidence: low", "the unfiled marker is status: unfiled"),
]


def scan(text: str) -> list:
    """Return [(needle, reason, line_number)] for every forbidden name found."""
    hits = []
    for needle, reason in FORBIDDEN:
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                hits.append((needle, reason, lineno))
                break
    return hits


def main(argv: list) -> int:
    path = Path(argv[0]).resolve() if argv else DEFAULT_TEMPLATE
    if not path.is_file():
        print(f"check-payload-layout-free: FAIL — no template at {path}", file=sys.stderr)
        return 1
    hits = scan(path.read_text(encoding="utf-8"))
    if hits:
        print(f"check-payload-layout-free: FAIL — {path} names {len(hits)} thing(s) the series moves:", file=sys.stderr)
        for needle, reason, lineno in hits:
            print(f"    {path.name}:{lineno}  {needle!r} — {reason}", file=sys.stderr)
        print("", file=sys.stderr)
        print("    The payload names index.md, standards/ and moc-projects.md and nothing else.", file=sys.stderr)
        return 1
    print(f"check-payload-layout-free: OK — {path.name} is layout-free ({len(FORBIDDEN)} names checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
