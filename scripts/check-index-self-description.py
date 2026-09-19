#!/usr/bin/env python3
"""Gate: `index.md` describes the vault, in the payload's terms (plan 12 task 1).

The map is the first thing every surface reads, and the twin that used to
explain the vault (`agent/_meta/how-to-use-agentmemory.md`) retired with
`_meta/`. So `index.md` owes the payload's reading order and its card guide —
the same words, minus the per-surface paragraph, which is the one part of the
payload that is about the reader rather than about the vault.

What this checks, and why each one:

  - The section exists. Without it an agent that opens the map learns the
    layout and nothing about how to read what it finds.
  - The reading order names the three things that never move: `standards/`,
    `moc-projects.md`, and the project's own `tracker.md` / `charter.md`.
    These are the payload's anchors precisely because every other name in the
    vault has moved at least once this series.
  - The card guide names every field in the card's order, and defines the four
    words a reader cannot guess: `unfiled`, `dormant`, `superseded`, and what
    `completed/` and `agent/archive/` mean. A reader who skips an `unfiled`
    note because the word sounds like "unfinished" is the failure here.
  - It names `standards/storage-rules.md` as the file that decides where a
    capture goes — a pointer, never a copy. The contract is the one gated
    source of truth for routing (`check-storage-rules` parses it, the daemon
    reads it at runtime, the taxonomy growth rule guards it), and none of that
    would cover a second copy sitting in the map.
  - It does not send a reader to `Filing.md`, which folded into `index.md` in
    plan 07. The pointer has to survive that fold; that is the whole reason
    the design chose a pointer over a copy.

It reads the live vault, resolved at runtime, and says so rather than guessing
when no vault resolves — CI has no vault and must not fail on its absence.

Usage:
  python3 scripts/check-index-self-description.py              # the resolved root
  python3 scripts/check-index-self-description.py --memory-root DIR
  python3 scripts/check-index-self-description.py --self-test  # prove it can fail
Exit: 0 clean (or no vault); 1 on a finding.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vault_layout  # noqa: E402

HEADING = "How to read this vault"

# (what must appear, what the reader loses without it). One row per obligation
# so a partial hole reads as loudly as a missing section.
REQUIRED = (
    ("standards/", "the reading order does not send anyone to the contract"),
    ("moc-projects.md", "a project question has nowhere current to land"),
    ("tracker.md", "the project's own state is unreachable from the map"),
    ("charter.md", "what a project *is* is unreachable from the map"),
    ("standards/storage-rules.md", "nothing names the file that decides where a capture goes"),
    ("title", "the card guide does not name the field a reader reads first"),
    ("type", "nothing distinguishes a memory from a record"),
    ("kind", "nothing distinguishes a record from a memory"),
    ("summary", "the card guide skips the one line worth reading first"),
    ("why", "nothing says the reason a note was kept is recorded"),
    ("importance", "the operator's own ranking is invisible"),
    ("status", "nothing explains the field that says whether a note is current"),
    ("lifecycle", "nothing explains the aging axis"),
    ("unfiled", "a reader skips real content because the word sounds unfinished"),
    ("dormant", "a year-unused note reads as a dead one"),
    ("superseded", "a reader answers from history instead of following the pointer"),
    ("completed/", "finished work reads as either current or wrong"),
    ("agent/archive/", "retired work reads as either current or wrong"),
)

# Named here rather than in REQUIRED because its absence is the finding.
RETIRED_POINTER = "Filing.md"


def section(text: str) -> str | None:
    """The body under the heading, up to the next same-or-higher heading."""
    m = re.search(rf"^##\s+{re.escape(HEADING)}\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^#{1,2}\s+\S", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def findings(index_md: Path) -> list:
    if not index_md.is_file():
        return [f"{index_md} does not exist"]
    body = section(index_md.read_text(encoding="utf-8"))
    if body is None:
        return [f"no '## {HEADING}' section in {index_md.name}"]
    out = []
    for token, cost in REQUIRED:
        if token not in body:
            out.append(f"the section never names `{token}` — {cost}")
    if RETIRED_POINTER in body:
        out.append(
            f"the section points at `{RETIRED_POINTER}`, which folded into "
            "this file in plan 07 — the pointer has to survive that fold")
    return out


def check(vault: Path, out=sys.stdout) -> int:
    found = findings(vault / "index.md")
    if not found:
        print("check-index-self-description: clean — index.md carries the "
              "payload's reading order and card guide", file=out)
        return 0
    print(f"check-index-self-description: {len(found)} finding(s)", file=out)
    for f in found:
        print(f"  {f}", file=out)
    return 1


def self_test(out=sys.stdout) -> int:
    """A gate nobody has seen fail is a gate nobody knows works."""
    import tempfile
    complete = "## How to read this vault\n\n" + " ".join(
        t for t, _ in REQUIRED) + "\n"
    with tempfile.TemporaryDirectory() as d:
        v = Path(d)
        (v / "index.md").write_text(complete, encoding="utf-8")
        if findings(v / "index.md"):
            print("check-index-self-description: SELF-TEST FAILED — a complete "
                  "section reported findings", file=out)
            return 1
        (v / "index.md").write_text(
            complete.replace("unfiled", "") + f"\nSee {RETIRED_POINTER}.\n",
            encoding="utf-8")
        got = findings(v / "index.md")
        if len(got) != 2:
            print("check-index-self-description: SELF-TEST FAILED — a section "
                  f"missing a term and naming the retired pointer gave {got}",
                  file=out)
            return 1
        (v / "index.md").write_text("## Something else\n\nbody\n", encoding="utf-8")
        if not findings(v / "index.md"):
            print("check-index-self-description: SELF-TEST FAILED — a missing "
                  "section reported clean", file=out)
            return 1
    print("check-index-self-description: self-test OK — a complete section "
          "passes, a hole and a retired pointer each fail", file=out)
    return 0


def _resolve(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the gate can fail, then exit")
    args = ap.parse_args(argv)
    rc = self_test()
    if args.self_test or rc:
        return rc
    root = _resolve(args.memory_root)
    if root is None or not root.is_dir():
        print("check-index-self-description: no vault resolves; nothing to check")
        return 0
    return check(vault_layout.vault_root_candidates(root)[0])


if __name__ == "__main__":
    raise SystemExit(main())
