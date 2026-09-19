#!/usr/bin/env python3
"""Gate: a pass that searches in order to grade searching says so (plan 12 task 6).

Since the daemon writes the clock for every hit it serves, `agentmd search` is
no longer free. A query is a statement that somebody read the note, and the note
is held at day zero for a year because of it.

Most callers in this repo are not somebody reading. The retrieval gate runs 64
questions a night against the same gold set; the scorecards, the probe, the
lifecycle eval, the alias pilot and the filing engine's similarity shortlist all
search without anybody looking at the result. Left unlabelled they would hold
roughly three hundred notes young in perpetuity — and the corpus would read as
healthy while the axis measured nothing.

So a measurement caller passes `-surface measure`, and this gate is what keeps
the next one from forgetting. It is a grep, deliberately: the failure is a
missing argument at a call site, and that is exactly what a grep can see.

**Why the default is not `measure`.** A person running `agentmd search` in a
terminal is reading their memory, and that is a genuine recall. The two failure
directions are not symmetric — a real recall uncounted ages a note the operator
is using, where a measurement counted keeps a note young, which is visible in
the morning note and costs nothing but rank. So the default serves the person
and the measurement opts out, with this gate holding the opt-out.

Usage:
  python3 scripts/check-measurement-surface.py
  python3 scripts/check-measurement-surface.py --self-test
Exit: 0 clean; 1 on a call site with no surface.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Files whose `agentmd search` calls are measurement. One list, named rather
# than pattern-matched: whether a caller is reading or grading is a judgment
# about what the caller is for, and a heuristic would get it wrong in the
# direction that silently ages the operator's notes.
MEASUREMENT_CALLERS = (
    "scripts/health/eval_retrieval_shipped.py",
    "scripts/health/retrieval_scorecard.py",
    "scripts/health/eval_lifecycle_separation.py",
    "scripts/health/zero_hit_probe.py",
    "scripts/alias_pilot.py",
    "harness/skills/memory/scripts/filing_engine.py",
)

# A shell-argv `search` invocation: the subcommand as its own quoted element.
_SEARCH_CALL = re.compile(r'''["']search["']\s*,''')
_HAS_SURFACE = re.compile(r'''["']-surface["']''')


def findings(repo: Path = _REPO) -> list:
    out = []
    for rel in MEASUREMENT_CALLERS:
        p = repo / rel
        if not p.is_file():
            out.append(f"{rel}: listed as a measurement caller but not in the tree — "
                       "remove the row or restore the file")
            continue
        text = p.read_text(encoding="utf-8")
        calls = len(_SEARCH_CALL.findall(text))
        if calls == 0:
            out.append(f"{rel}: no `agentmd search` call found — if it stopped "
                       "searching, drop it from MEASUREMENT_CALLERS")
            continue
        if len(_HAS_SURFACE.findall(text)) < calls:
            out.append(
                f"{rel}: {calls} search call(s), "
                f"{len(_HAS_SURFACE.findall(text))} carrying `-surface`. A "
                "measurement query with no surface resets the clock of every "
                "note it returns, and holds it there.")
    return out


def self_test(out=sys.stdout) -> int:
    """The gate has to be seen refusing something."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        rel = MEASUREMENT_CALLERS[0]
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('argv = [b, "search", "-json", "-k", "5"]\n', encoding="utf-8")
        got = [x for x in findings(repo) if x.startswith(rel)]
        if not got:
            print("check-measurement-surface: SELF-TEST FAILED — a call with no "
                  "surface passed", file=out)
            return 1
        f.write_text('argv = [b, "search", "-json", "-surface", "measure"]\n',
                     encoding="utf-8")
        if [x for x in findings(repo) if x.startswith(rel)]:
            print("check-measurement-surface: SELF-TEST FAILED — a labelled call "
                  "was refused", file=out)
            return 1
    print("check-measurement-surface: self-test OK — an unlabelled call fails "
          "and a labelled one passes", file=out)
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    rc = self_test()
    if args.self_test or rc:
        return rc
    found = findings()
    if not found:
        print(f"check-measurement-surface: clean — {len(MEASUREMENT_CALLERS)} "
              "measurement caller(s), every search labelled")
        return 0
    print(f"check-measurement-surface: {len(found)} finding(s)")
    for f in found:
        print(f"  {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
