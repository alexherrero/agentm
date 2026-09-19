#!/usr/bin/env python3
"""Gate: the `/memory` table names what the skill actually does (plan 12 task 8).

The table at the top of `harness/skills/memory/SKILL.md` is the only list of
sub-commands anywhere. Nothing generates it, so it drifts the way an unchecked
list always drifts — the line above it said "four sub-commands
(`save` / `evolve` / `reflect` / `search`)" through roughly twenty additions,
and `search` was a stub for most of them.

Two directions, and both matter:

  - **A row with no section.** The table promises something the document does
    not explain. A reader follows the row and finds nothing.
  - **A section with no row.** The skill can do something nobody reading the
    table would know about. That is how `crystallize` sat undiscoverable with a
    working implementation behind it.

Some sections are deliberately not in the table, and they are named here rather
than inferred, because "not in the table" is a decision somebody made and the
gate should be able to tell it from an oversight.

Usage:
  python3 scripts/check-memory-skill-table.py
  python3 scripts/check-memory-skill-table.py --self-test
Exit: 0 clean; 1 on a drift.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
SKILL = _REPO / "harness" / "skills" / "memory" / "SKILL.md"

# A section the table deliberately does not carry, and why. The reason is the
# point: it is what lets a later reader tell a decision from a hole.
OFF_TABLE = {
    "diary": ("the register's front door is the calendar, not this skill "
              "(agentm-vault plan 12); the writer stays because the other "
              "facets use it"),
    "promote": ("the incubator it graduates from is retired; the ideas surface "
                "is rebuilt in plan 13, and documenting the old shape now would "
                "be documenting something about to change"),
}

_TABLE_START = "| You want to... | Reach for |"
_TABLE_END = "Auto-recall happens via"
_CMD = re.compile(r"`/memory ([a-z-]+(?: --[a-z]+)?)`")
_SECTION = re.compile(r"^### `/memory ([a-z-]+(?: --[a-z]+)?)`", re.M)


def findings(text: str, *, check_off_table: bool = True) -> list:
    """`check_off_table` is off for the self-test, whose synthetic document has
    none of the real sections and would report every OFF_TABLE row as stale —
    a gate whose self-test can only pass by weakening the gate is worse than
    none, so the flag scopes the staleness check rather than removing it."""
    out = []
    try:
        body = text[text.index(_TABLE_START):text.index(_TABLE_END, text.index(_TABLE_START))]
    except ValueError:
        return ["the sub-command table is not where this gate looks for it — "
                f"expected a line beginning {_TABLE_START!r}"]
    rows = set(_CMD.findall(body))
    sections = set(_SECTION.findall(text))

    for cmd in sorted(rows - sections):
        # A flagged form (`search --deep`) is documented inside its base
        # command's section, which is the right place for it.
        base = cmd.split(" ")[0]
        if base in sections and f"/memory {cmd}" in text:
            continue
        out.append(f"the table offers `/memory {cmd}` and nothing documents it")

    for cmd in sorted(sections - rows):
        if cmd in OFF_TABLE:
            continue
        out.append(
            f"`/memory {cmd}` is documented and not in the table — a reader of "
            "the table cannot discover it. Add a row, or add it to OFF_TABLE "
            "with the reason it is deliberately absent.")

    for cmd, why in sorted(OFF_TABLE.items() if check_off_table else ()):
        if cmd not in sections:
            out.append(f"OFF_TABLE names `/memory {cmd}` ({why}), which no "
                       "longer has a section — drop the row")
    return out


def self_test(out=sys.stdout) -> int:
    base = ("| You want to... | Reach for |\n|---|---|\n"
            "| do a thing | `/memory alpha` |\n\nAuto-recall happens via x.\n\n"
            "### `/memory alpha`\n\nbody\n")
    if findings(base, check_off_table=False):
        print("check-memory-skill-table: SELF-TEST FAILED — a matching pair "
              "reported drift", file=out)
        return 1
    if not findings(base + "\n### `/memory beta`\n\nbody\n", check_off_table=False):
        print("check-memory-skill-table: SELF-TEST FAILED — an undiscoverable "
              "section passed", file=out)
        return 1
    orphan = base.replace("`/memory alpha` |", "`/memory gamma` |")
    if not findings(orphan, check_off_table=False):
        print("check-memory-skill-table: SELF-TEST FAILED — a row with no "
              "section passed", file=out)
        return 1
    print("check-memory-skill-table: self-test OK — a matching pair passes, and "
          "drift in either direction fails", file=out)
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    rc = self_test()
    if args.self_test or rc:
        return rc
    if not SKILL.is_file():
        print(f"check-memory-skill-table: no skill at {SKILL}; nothing to check")
        return 0
    found = findings(SKILL.read_text(encoding="utf-8"))
    if not found:
        print("check-memory-skill-table: clean — every row has a section, every "
              f"section has a row or a named reason ({len(OFF_TABLE)} off-table)")
        return 0
    print(f"check-memory-skill-table: {len(found)} finding(s)")
    for f in found:
        print(f"  {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
