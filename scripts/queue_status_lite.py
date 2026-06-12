#!/usr/bin/env python3
"""queue_status_lite — a read-only dashboard of every active plan in `_harness/`.

The coordinator's glance: enumerate each active plan (`PLAN.md` plus every named
`PLAN-<name>.md`) and, for each, print its name, its `Status:` line, and the
most-recent entry of the matching `progress*.md`.

**Read-only by contract** (V5-10 design call): no claim arbitration, no leases,
no writes — the human is the arbiter. This is the agentm read logic; the crickets
`/queue-status-lite` command surface (a later sibling plan) wraps it.

Usage:

    python3 queue_status_lite.py [--harness-dir PATH]

With no `--harness-dir`, the directory is resolved from the cwd (vault-backed, or
`<repo>/.harness/` in local mode). Always exits 0 — a status read, not a gate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

_PROGRESS_HEAD_MAXLEN = 120


class PlanStatus(NamedTuple):
    """One row of the dashboard."""

    plan_name: str  # "PLAN.md" / "PLAN-foo.md"
    status: str  # value of the Status: line, or "—"
    progress_name: str  # "progress.md" / "progress-foo.md"
    progress_head: str  # most-recent progress entry, or a "(…)" placeholder


def _plan_sort_key(name: str) -> tuple[int, str]:
    """Singleton `PLAN.md` first, then named plans alphabetically — deterministic."""
    return (0, "") if name == "PLAN.md" else (1, name)


def list_plan_files(harness_dir: Path) -> list[Path]:
    """Every *active* plan file in `harness_dir`: the singleton `PLAN.md` plus each
    `PLAN-<name>.md`.

    Excludes archived plans (`PLAN.archive.*.md` — they start with `PLAN.`, not
    `PLAN-`, so the `PLAN-*` glob already skips them) and GDrive conflict artifacts
    (`PLAN-foo (conflicted copy …).md` — the conflict-janitor's domain, surfaced
    via `hm._conflict_family`, not an active plan).
    """
    files: list[Path] = []
    singleton = harness_dir / "PLAN.md"
    if singleton.is_file():
        files.append(singleton)
    for p in harness_dir.glob("PLAN-*.md"):
        if p.is_file() and hm._conflict_family(p.name) is None:
            files.append(p)
    return sorted(files, key=lambda p: _plan_sort_key(p.name))


def _extract_status(plan_text: str) -> str:
    """The value of the first `Status:` line (markdown-bold tolerated), or "—"."""
    for line in plan_text.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped.lower().startswith("status:"):
            value = stripped[len("status:"):].strip().strip("*").strip()
            return value or "—"
    return "—"


def _progress_head(path: Path) -> str:
    """The most-recent (last non-empty) line of an append-only progress log."""
    if not path.is_file():
        return "(no progress file)"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(unreadable)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "(empty)"
    head = lines[-1]
    if len(head) > _PROGRESS_HEAD_MAXLEN:
        head = head[: _PROGRESS_HEAD_MAXLEN - 1].rstrip() + "…"
    return head


def collect_plan_statuses(harness_dir: Path) -> list[PlanStatus]:
    """Read-only: build a `PlanStatus` row for each active plan in `harness_dir`.

    No writes, no mutation — the directory is byte-identical after this call. The
    PLAN→progress filename mapping reuses the centralized resolver helpers
    (`hm._normalize_plan_name` / `hm._plan_pair`) so the naming contract lives in
    exactly one place (the contract task 1/2 lock).
    """
    rows: list[PlanStatus] = []
    for plan_path in list_plan_files(harness_dir):
        plan_name = plan_path.name
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except OSError:
            plan_text = ""
        status = _extract_status(plan_text)
        progress_name = hm._plan_pair(hm._normalize_plan_name(plan_name))[1]
        progress_head = _progress_head(harness_dir / progress_name)
        rows.append(PlanStatus(plan_name, status, progress_name, progress_head))
    return rows


def render(harness_dir: Path, rows: list[PlanStatus]) -> str:
    """A deterministic, human-scannable block. Output depends only on
    `harness_dir`'s contents — no wall-clock, no color — so it is test-stable."""
    if not rows:
        return f"No plans found in {harness_dir}\n"
    width = max(len(r.plan_name) for r in rows)
    lines = [f"Active plans in {harness_dir}:", ""]
    for r in rows:
        lines.append(f"  {r.plan_name:<{width}}  [{r.status}]")
        lines.append(f"  {'':<{width}}  last: {r.progress_head}")
    return "\n".join(lines) + "\n"


def _resolve_harness_dir(explicit: Optional[str]) -> Optional[Path]:
    if explicit is not None:
        return Path(explicit)
    resolution = hm.resolve_project({"cwd": Path.cwd()})
    return hm.harness_state_dir(resolution)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="queue_status_lite",
        description="Read-only dashboard of every active plan in _harness/.",
    )
    parser.add_argument(
        "--harness-dir",
        default=None,
        help="the _harness/ directory to enumerate (default: resolve from cwd).",
    )
    args = parser.parse_args(argv)

    harness_dir = _resolve_harness_dir(args.harness_dir)
    if harness_dir is None or not harness_dir.is_dir():
        # Graceful: no resolvable _harness/ is not an error for a status read.
        where = harness_dir if harness_dir is not None else "(unresolved)"
        print(f"No _harness/ directory to read ({where}).")
        return 0

    rows = collect_plan_statuses(harness_dir)
    sys.stdout.write(render(harness_dir, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
