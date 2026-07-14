#!/usr/bin/env python3
"""status.py — the Operator `/status` surface (PLAN-wave-e-scheduled-surfaces
task 5; ROADMAP-MASTER.md:121, ratified verbatim: "Wave-E's Operator `/status`
+ the reporting digest consume R's scorecard rather than re-deriving one.").

Reads the last row of the health-history ledger (health_score.py's own
persisted ledger, `resolve_history_path()` — the vault when one resolves,
else a device-local fallback) and prints the Health Index, the per-family
breakdown, and the dark-check count — on demand, no new scoring logic. This
is a consumer, never a second scorer: every number printed here is read back
from a row health_score.py already computed and wrote; nothing is recomputed.

Run directly: `cd scripts && python3 status.py`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "health"))

import health_score  # noqa: E402


def render_status(row: dict) -> str:
    lines = [f"Health Index: {row['health_index']:.2f}", "", "Families:"]
    for axis, score in sorted(row["families"].items()):
        lines.append(f"  {axis}: {score:.2f}")
    lines.append("")
    # Older history rows (recorded before this field existed) don't carry
    # dark_count — surface that plainly rather than raising or guessing.
    dark_count = row.get("dark_count")
    lines.append(f"Dark checks: {dark_count if dark_count is not None else 'not recorded for this run'}")
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="status", description="Surface the Health Index from the last recorded run.")
    p.add_argument("--path", default=None,
                    help="read from this history.jsonl instead of scripts/health/history.jsonl")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    path = Path(args.path) if args.path else None
    row = health_score.read_latest_history_row(path)
    if row is None:
        print("status: no health history yet — run `bash scripts/health/run-fast-tier.sh "
              "| python3 scripts/health/health_score.py --history` at least once",
              file=sys.stderr)
        return 1
    print(render_status(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
