#!/usr/bin/env python3
"""merge_order — merge-order recommendation for the team-coordinator persona (V5-11 task 5).

Takes plans whose tasks are all done (``tasks_done == tasks_total``, at least one
task) and produces an ordered list with a per-plan reason.

Ordering rules (applied in priority order):

  1. **Dependency order** — topo-sort by ``depends_on`` edges; a plan that
     other plans depend on appears earlier.  Cycles are detected and reported as
     an error (never silently broken).

  2. **Smallest-change tie-break** — among topologically equivalent plans, sort
     by ``git diff --stat worker/<slug>..main`` line-count ascending (smallest
     diff first — cheap to undo if bad; bigger changes land on a clean base).
     Worker-branch convention: ``worker/<slug>`` (same convention as
     ``spawn_worker.py``).

  3. **Deterministic fallback** — when ``git`` is unavailable, returns a non-zero
     exit code, or a branch doesn't exist, skip the tie-break and sort by plan
     slug alphabetically.  The output is always deterministic.

**Advisory only / read-only.**  Zero git writes.  The operator applies the order.

Usage::

    python3 scripts/merge_order.py [--harness-dir PATH] [--json]
    python3 scripts/merge_order.py [--harness-dir PATH] [--json] [--no-git]

``--no-git`` forces the alphabetical fallback (useful in tests and CI).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402
import plan_graph as pg  # noqa: E402

_WORKER_BRANCH_PREFIX = "worker/"


def _git_diff_stat_lines(slug: str) -> Optional[int]:
    """Return the total changed-line count for ``worker/<slug>..main``.

    Returns None when git is unavailable, the branch doesn't exist, or the
    command fails.  Callers must treat None as "use fallback sort".
    """
    branch = f"{_WORKER_BRANCH_PREFIX}{slug}"
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{branch}..main"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # The last non-empty line of `git diff --stat` is a summary line like
    # "  3 files changed, 42 insertions(+), 7 deletions(-)"
    # We sum insertions + deletions as the "size" proxy.
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return 0
    summary = lines[-1]
    total = 0
    for word, _ in zip(summary.split(), range(20)):
        pass  # walk past; we parse by keyword
    import re as _re
    for m in _re.finditer(r"(\d+)\s+(?:insertion|deletion)", summary):
        total += int(m.group(1))
    return total


def _topo_sort(
    slugs: List[str],
    depends_on: Dict[str, List[str]],
) -> List[str]:
    """Kahn's algorithm — plans that others depend on go first.

    Raises ``ValueError`` if a cycle is detected.
    """
    # Build in-degree and adjacency from the finished-plan perspective.
    # Edge direction: if B is in A's depends_on, then B → A (B must come before A).
    in_degree: Dict[str, int] = {s: 0 for s in slugs}
    dependents: Dict[str, List[str]] = {s: [] for s in slugs}

    for slug in slugs:
        for dep in depends_on.get(slug, []):
            if dep not in in_degree:
                continue  # dep not in the finished set — ignore missing edge
            dependents[dep].append(slug)
            in_degree[slug] += 1

    # Process in alphabetical order within each level for determinism.
    queue = sorted(s for s, deg in in_degree.items() if deg == 0)
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for dep_of_node in sorted(dependents[node]):
            in_degree[dep_of_node] -= 1
            if in_degree[dep_of_node] == 0:
                queue.append(dep_of_node)
                queue.sort()

    if len(result) != len(slugs):
        remaining = set(slugs) - set(result)
        raise ValueError(f"cycle detected in depends_on graph: {sorted(remaining)}")

    return result


def build_merge_order(
    harness_dir: Path,
    use_git: bool = True,
) -> List[dict]:
    """Return the recommended merge order for finished plans.

    Each entry: ``{slug: str, reason: str}``.
    Raises ``ValueError`` on a dep cycle.
    """
    plans = pg.build_plan_graph(harness_dir)

    # Finished = all tasks done (and at least one task exists).
    finished = [
        p for p in plans
        if p.tasks_total > 0 and p.tasks_done == p.tasks_total
    ]

    if not finished:
        return []

    slugs = [p.slug for p in finished]
    dep_map = {p.slug: p.depends_on for p in finished}

    # Topo sort.
    topo_order = _topo_sort(slugs, dep_map)

    # Group by topo level to apply the size tie-break within each level.
    # Levels: plans at position 0 are "level 0" (no in-level deps); etc.
    # We compute levels by iterating the sorted order and assigning level based
    # on the max level of any dep.
    level_map: Dict[str, int] = {}
    for slug in topo_order:
        dep_levels = [
            level_map[d] for d in dep_map.get(slug, []) if d in level_map
        ]
        level_map[slug] = (max(dep_levels) + 1) if dep_levels else 0

    # Within each level, sort by diff size (ascending) or slug (fallback).
    size_map: Dict[str, Optional[int]] = {}
    if use_git:
        for slug in topo_order:
            size_map[slug] = _git_diff_stat_lines(slug)
    else:
        for slug in topo_order:
            size_map[slug] = None

    def _sort_key(slug: str) -> Tuple:
        size = size_map.get(slug)
        if size is None:
            # Fallback: sort by slug alphabetically (deterministic).
            return (level_map[slug], 1, slug)
        return (level_map[slug], 0, size, slug)

    ordered = sorted(topo_order, key=_sort_key)

    # Build reasons.
    result: List[dict] = []
    for slug in ordered:
        deps = [d for d in dep_map.get(slug, []) if d in set(slugs)]
        size = size_map.get(slug)
        if deps and size is not None:
            reason = (
                f"deps ({', '.join(deps)}) must land first; "
                f"{size} changed lines (smaller diff)"
            )
        elif deps:
            reason = f"deps ({', '.join(deps)}) must land first"
        elif size is not None:
            reason = f"{size} changed lines (smaller diff lands first)"
        else:
            reason = "no deps; alphabetical order (git unavailable)"
        result.append({"slug": slug, "reason": reason})

    return result


def render_text(order: List[dict]) -> str:
    if not order:
        return "(no finished plans to order)"
    lines = []
    for i, entry in enumerate(order, 1):
        lines.append(f"  {i}. {entry['slug']} — {entry['reason']}")
    return "\n".join(["Recommended merge order:", *lines])


def _main() -> None:
    ap = argparse.ArgumentParser(
        description="Merge-order recommendation for finished plans."
    )
    ap.add_argument("--harness-dir", help="Path to the _harness/ directory.")
    ap.add_argument(
        "--json", action="store_true",
        help="Emit JSON array instead of the human-readable summary.",
    )
    ap.add_argument(
        "--no-git", action="store_true",
        help="Skip git diff tie-break (use alphabetical fallback).",
    )
    args = ap.parse_args()

    if args.harness_dir:
        harness_dir = Path(args.harness_dir)
    else:
        harness_dir = hm.harness_state_dir()

    try:
        order = build_merge_order(harness_dir, use_git=not args.no_git)
    except ValueError as exc:
        print(f"merge_order: ERROR — {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.json:
        print(json.dumps(order, indent=2))
    else:
        print(render_text(order))


if __name__ == "__main__":
    _main()
