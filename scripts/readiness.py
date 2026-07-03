#!/usr/bin/env python3
"""readiness — dispatch recommendation for the team-coordinator persona (V5-11 task 4).

Two-stage check:

  Stage 1 — Ready?
    A queued plan is *ready* if every plan slug in its ``depends_on`` list
    has ``Status: done`` in the current plan graph.

  Stage 2 — Safe together?
    Among ready plans, find pairs whose ``touches:`` glob lists are disjoint
    (set-intersection after glob expansion = ∅).  A plan with no ``touches:``
    field is **excluded** from the safe set and generates a loud degrade warning
    — never silently included, never guessed.

**Advisory only / read-only.**  Zero writes to disk.

Usage::

    python3 scripts/readiness.py [--harness-dir PATH] [--json]

Return structure (JSON)::

    {
      "ready":           [slug, ...],
      "safe_together":   [slug, ...],
      "held_back":       [{slug, reason}, ...],
      "degrade_warnings": [str, ...]
    }
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402
import plan_graph as pg  # noqa: E402

_DEGRADE_MSG = (
    "plan '{slug}' excluded from safe-to-run-together check — "
    "touches: not declared; add it to get a file-overlap verdict"
)


def _glob_expand(patterns: List[str]) -> frozenset[str]:
    """Return the patterns themselves as the 'expanded' set.

    We deliberately do NOT walk the filesystem here — the safe-to-run-together
    check compares declared intent, not on-disk state, because the work hasn't
    happened yet.  Pattern-level set intersection is the right comparison:
    if any declared pattern from plan A matches any declared pattern from plan B
    (using fnmatch), the plans potentially overlap.
    """
    return frozenset(patterns)


def _patterns_overlap(a: List[str], b: List[str]) -> bool:
    """True if any pattern in *a* fnmatch-matches any pattern in *b* or vice-versa.

    Both directions are checked because ``fnmatch("src/api/**", "src/**")``
    differs from ``fnmatch("src/**", "src/api/**")``.
    """
    for pa in a:
        for pb in b:
            if fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa):
                return True
    return False


def build_readiness(harness_dir: Path) -> dict:
    """Compute the readiness report for queued plans in *harness_dir*.

    Returns a plain dict with keys ``ready``, ``safe_together``,
    ``held_back``, and ``degrade_warnings``.
    """
    plans = pg.build_plan_graph(harness_dir)
    by_slug = {p.slug: p for p in plans}

    ready: List[str] = []
    held_back: List[dict] = []
    degrade_warnings: List[str] = []

    # --- Stage 1: dependency check on queued plans ---
    queued_plans = [p for p in plans if not p.active]
    if not queued_plans:
        # Also check active plans that haven't started (status == "planning").
        queued_plans = [p for p in plans if p.active and p.status == "planning"]

    for plan in queued_plans:
        unmet = []
        for dep_slug in plan.depends_on:
            dep = by_slug.get(dep_slug)
            if dep is None or dep.status != "done":
                unmet.append(dep_slug)
        if unmet:
            held_back.append({
                "slug": plan.slug,
                "reason": f"waiting for: {', '.join(unmet)}",
            })
        else:
            ready.append(plan.slug)

    # --- Stage 2: overlap check among ready plans ---
    # Separate those with touches: declared from those without.
    with_touches = [s for s in ready if by_slug[s].touches]
    without_touches = [s for s in ready if not by_slug[s].touches]

    for slug in without_touches:
        degrade_warnings.append(_DEGRADE_MSG.format(slug=slug))

    # Find the safe-to-run-together set among plans with touches declared.
    # A plan is safe to run together with all others if its touches list is
    # disjoint from every other plan's touches list.
    safe_together: List[str] = []
    for slug in with_touches:
        overlaps = [
            other for other in with_touches
            if other != slug
            and _patterns_overlap(by_slug[slug].touches, by_slug[other].touches)
        ]
        if overlaps:
            held_back.append({
                "slug": slug,
                "reason": (
                    f"touches overlap with: {', '.join(overlaps)}"
                ),
            })
        else:
            safe_together.append(slug)

    return {
        "ready": ready,
        "safe_together": safe_together,
        "held_back": held_back,
        "degrade_warnings": degrade_warnings,
    }


def render_text(report: dict) -> str:
    """Human-readable summary of the readiness report."""
    lines: List[str] = []

    ready = report["ready"]
    safe = report["safe_together"]
    held = report["held_back"]
    warns = report["degrade_warnings"]

    if not ready:
        lines.append("No plans are ready to start (all queued plans have unmet deps).")
    else:
        lines.append(f"Ready to start ({len(ready)}): {', '.join(ready)}")

    if safe:
        lines.append(f"Safe to run together ({len(safe)}): {', '.join(safe)}")
    elif ready:
        lines.append("None confirmed safe to run together (see held/degrade below).")

    if held:
        lines.append("\nHeld back:")
        for h in held:
            lines.append(f"  {h['slug']}: {h['reason']}")

    if warns:
        lines.append("\nDegrade warnings:")
        for w in warns:
            lines.append(f"  WARNING: {w}")

    return "\n".join(lines)


def _main() -> None:
    ap = argparse.ArgumentParser(
        description="Readiness + safe-to-run-together check for queued plans."
    )
    ap.add_argument("--harness-dir", help="Path to the _harness/ directory.")
    ap.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of the human-readable summary.",
    )
    args = ap.parse_args()

    if args.harness_dir:
        harness_dir = Path(args.harness_dir)
    else:
        harness_dir = hm.harness_state_dir(hm.resolve_project({"cwd": Path.cwd()}))
        if harness_dir is None:
            print(
                "readiness: could not resolve a _harness/ directory for this "
                "project (no synced backend, no device-local project root) — "
                "pass --harness-dir explicitly",
                file=sys.stderr,
            )
            raise SystemExit(1)

    report = build_readiness(harness_dir)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))


if __name__ == "__main__":
    _main()
