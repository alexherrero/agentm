#!/usr/bin/env python3
"""run_all.py — drive the four D-⑫ seeded task-pair checkers
(PLAN-r3-uplift-scoring task 3 / R3.2a).

--dry-run (fixture mode, no live model calls): each checker runs against its
own hand-constructed backed/bare fixture pair, asserts it discriminates
(backed=True, bare=False), and reports N/4 wired. Fast-tier safe.

Without --dry-run: intended for Task 4's (R3.2b) live weekly runner, which
feeds `check()` real bare-vs-backed model transcripts instead of fixtures —
not implemented here (that's the scheduled workflow's own script).

Usage:
    python3 scripts/health/probes/run_all.py --dry-run
Exit:
    0  all checkers correctly discriminate their own fixture pair
    1  at least one checker failed to discriminate (backed != True or bare != False)
    2  usage error
"""
from __future__ import annotations

import argparse
import sys

try:
    from . import checkers as _checkers_pkg
    ALL_PROBES = _checkers_pkg.ALL_PROBES
except ImportError:  # run as a script, not a package (python3 run_all.py)
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from checkers import ALL_PROBES  # type: ignore


def run_dry_run() -> int:
    wired = 0
    failures: list[str] = []
    for probe in ALL_PROBES:
        backed_result = probe.check(probe.backed_fixture())
        bare_result = probe.check(probe.bare_fixture())
        if backed_result is True and bare_result is False:
            wired += 1
            print(f"  PASS  {probe.NAME}: discriminates (backed=True, bare=False)")
        else:
            failures.append(
                f"{probe.NAME}: backed={backed_result!r} bare={bare_result!r} "
                "(expected backed=True, bare=False)"
            )
            print(f"  FAIL  {probe.NAME}: backed={backed_result!r} bare={bare_result!r}")

    print()
    print(f"run_all: {wired}/{len(ALL_PROBES)} checkers wired")
    if failures:
        for f in failures:
            print(f"  ↳ {f}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="fixture mode, no live model calls")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.dry_run:
        print("run_all: only --dry-run (fixture mode) is implemented here; "
              "the live weekly runner is Task 4's scheduled workflow.", file=sys.stderr)
        return 2

    return run_dry_run()


if __name__ == "__main__":
    sys.exit(main())
