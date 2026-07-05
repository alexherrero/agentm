#!/usr/bin/env python3
"""health_score.py — aggregate verify-suite JSONL check records into the
health scorecard (R1.8 / dashboard v1).

Reads check records (one JSON object per line) from stdin or a file path.
Record schema (locked in PLAN-r1-regression-net.md's Locked design calls):

    {"suite": str, "axis": str, "check": str, "pass": bool, "weight": float}

Dark checks (R1.8 Task 5 — designed-not-built capabilities) use:

    {"suite": str, "axis": str, "check": str, "pass": null, "dark": true, "weight": float}

Ablation records (PLAN-r3-uplift-scoring Task 2 / R3.1b — mechanical-uplift
baselines) use a separate schema, read from `--ablation-records` and never
merged into the scored `records` list:

    {"subsystem": str, "axis": str, "score_on": float, "score_off": float, "uplift": float}

Rendered as their own additive "Mechanical uplift" section — like dark
checks, never folded into the family-weighted Health Index.

`axis` is one of the eight locked family names below — v1 has no separate
axis→family rollup; a check's axis IS its family.

Scoring (locked):
    axis score  = 100 * sum(weight for pass) / sum(weight)   [dark checks excluded]
    family      = its axis score (v1: one axis per family)
    Health Index = weighted mean of family scores, weighted by FAMILY_WEIGHTS,
                   over only the families that have at least one non-dark record
                   (a family nothing exercised yet doesn't drag down the index —
                   same non-punitive principle as dark checks, generalized)

Stdlib only (no third-party deps — this must run everywhere check-all.sh runs).

Usage:
    <producer> | python3 scripts/health/health_score.py [--path FILE]
                                                          [--dark-checks FILE]
                                                          [--history]
                                                          [--check-determinism]
Exit:
    0  scorecard rendered (or determinism check passed)
    1  determinism check failed (two runs at the same input produced different output)
    2  setup error (bad input, no records)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HISTORY_PATH = HERE / "history.jsonl"

# Locked family weights (PLAN-r1-regression-net.md's Locked design calls).
# Keys are the exact `axis` string a check record carries.
FAMILY_WEIGHTS: dict[str, float] = {
    "memory persist+recall": 25,
    "plan-adherence+drift": 15,
    "verification honesty": 15,
    "capability function": 15,
    "memory freshness+experience": 10,
    "efficiency": 10,
    "docs+voice health": 5,
    "safety/recoverability": 5,
}

# Bumping either version starts a new baseline row (Locked design calls:
# "History row immutability") — scores only compare within the same bucket.
FIXTURE_PACK_VERSION = "v1"
RULE_PACK_VERSION = "v1"


def read_records(path: str | None) -> list[dict]:
    """Parse JSONL check records from `path`, or stdin if `path` is None.

    Blank lines are skipped. Raises ValueError on a line that isn't a JSON
    object (a setup error the caller should surface, not swallow).
    """
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    records = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: not valid JSON: {e}") from e
        if not isinstance(record, dict):
            raise ValueError(f"line {lineno}: expected a JSON object, got {type(record).__name__}")
        records.append(record)
    return records


def _is_dark(record: dict) -> bool:
    return bool(record.get("dark")) or record.get("pass") is None


def score_axis(records: list[dict]) -> tuple[float, int, int]:
    """Return (score 0-100, live_count, dark_count) for one axis's records.

    Dark checks are excluded from both numerator and denominator (Locked
    design calls: "Dark checks do not reduce denominators").
    """
    live = [r for r in records if not _is_dark(r)]
    dark_count = len(records) - len(live)
    if not live:
        return 0.0, 0, dark_count
    total_weight = sum(float(r.get("weight", 1.0)) for r in live)
    if total_weight <= 0:
        return 0.0, len(live), dark_count
    achieved = sum(float(r.get("weight", 1.0)) for r in live if r.get("pass"))
    return 100.0 * achieved / total_weight, len(live), dark_count


def compute_scorecard(records: list[dict]) -> dict:
    """Aggregate records into a scorecard dict: per-family scores + Health Index."""
    by_axis: dict[str, list[dict]] = {}
    for r in records:
        axis = r.get("axis", "")
        by_axis.setdefault(axis, []).append(r)

    families = []
    weighted_sum = 0.0
    weight_total = 0.0
    for axis, weight in FAMILY_WEIGHTS.items():
        axis_records = by_axis.get(axis, [])
        score, live_count, dark_count = score_axis(axis_records)
        families.append({
            "axis": axis,
            "weight": weight,
            "score": round(score, 2),
            "live_count": live_count,
            "dark_count": dark_count,
        })
        if live_count > 0:
            weighted_sum += weight * score
            weight_total += weight

    unknown_axes = sorted(set(by_axis) - set(FAMILY_WEIGHTS))
    health_index = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0.0
    dark_checks = [
        {"axis": r.get("axis", ""), "suite": r.get("suite", ""), "check": r.get("check", "")}
        for r in records
        if _is_dark(r)
    ]

    return {
        "families": families,
        "health_index": health_index,
        "unknown_axes": unknown_axes,
        "total_records": len(records),
        "dark_checks": dark_checks,
    }


def _regression_headline(scorecard: dict, baseline: dict | None) -> str:
    """Locked regression rule: any family -3 pts vs last green -> red headline.
    (Blocker-tier-check-red is enforced by the caller passing pre-filtered
    input for that check; this function only applies the family-delta rule.)
    """
    if baseline is None:
        return "green"
    baseline_by_axis = {f["axis"]: f["score"] for f in baseline.get("families", [])}
    for f in scorecard["families"]:
        prior = baseline_by_axis.get(f["axis"])
        if prior is not None and f["score"] < prior - 3:
            return "red"
    return "green"


def render_markdown(scorecard: dict, *, headline: str = "green", ablation_records: list[dict] | None = None) -> str:
    lines = []
    marker = "🔴" if headline == "red" else "🟢"
    lines.append(f"# Health Scorecard {marker}")
    lines.append("")
    lines.append(f"**Health Index: {scorecard['health_index']}/100**")
    lines.append("")
    lines.append("| Family | Weight | Score | Checks | Dark |")
    lines.append("|---|---:|---:|---:|---:|")
    for f in scorecard["families"]:
        lines.append(
            f"| {f['axis']} | {f['weight']} | {f['score']:.2f} | {f['live_count']} | {f['dark_count']} |"
        )
    if scorecard["unknown_axes"]:
        lines.append("")
        lines.append(
            "> [!WARNING]\n"
            "> Unrecognized axis name(s) not in the locked family-weight table "
            f"(excluded from Health Index): {', '.join(scorecard['unknown_axes'])}"
        )
    if scorecard.get("dark_checks"):
        lines.append("")
        lines.append("## Dark checks (designed, not built)")
        lines.append("")
        lines.append(
            "Not counted for or against the Health Index — visible so a family's "
            "true future shape stays legible before its capability ships."
        )
        lines.append("")
        lines.append("| Axis | Suite | Check |")
        lines.append("|---|---|---|")
        for d in scorecard["dark_checks"]:
            lines.append(f"| {d['axis']} | {d['suite']} | {d['check']} |")
    if ablation_records:
        lines.append("")
        lines.append("## Mechanical uplift")
        lines.append("")
        lines.append(
            "How much each subsystem contributes vs a bare baseline (on-state "
            "score minus its own ABLATE_* off-state score). Additive — never "
            "counted for or against the Health Index; a dead or noisy "
            "subsystem shows up here as lost uplift, not a shifted average."
        )
        lines.append("")
        lines.append("| Subsystem | Axis | Score (on) | Score (off) | Uplift |")
        lines.append("|---|---|---:|---:|---:|")
        for a in ablation_records:
            lines.append(
                f"| {a['subsystem']} | {a['axis']} | {a['score_on']:.2f} | "
                f"{a['score_off']:.2f} | {a['uplift']:.2f} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def append_history_row(scorecard: dict, *, ts: int | None = None) -> dict:
    row = {
        "agentm_sha": _git_sha(),
        "fixture_pack_version": FIXTURE_PACK_VERSION,
        "rule_pack_version": RULE_PACK_VERSION,
        # Integer epoch seconds, not a float: a fractional tail can coincidentally
        # match the phone-us regex in check-no-pii.sh.
        "ts": ts if ts is not None else int(time.time()),
        "health_index": scorecard["health_index"],
        "families": {f["axis"]: f["score"] for f in scorecard["families"]},
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate verify-suite JSONL into the health scorecard.")
    p.add_argument("--path", default=None, help="read JSONL from this file instead of stdin")
    p.add_argument("--dark-checks", default=None, help="path to a JSONL file of dark (designed-not-built) check records to merge in")
    p.add_argument("--ablation-records", default=None, help="path to a JSONL file of mechanical-uplift ablation records (subsystem/axis/score_on/score_off/uplift) — rendered additively, never scored")
    p.add_argument("--history", action="store_true", help="append a row to scripts/health/history.jsonl")
    p.add_argument("--check-determinism", action="store_true", help="run twice against the same input; exit non-zero if outputs differ")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.check_determinism:
        try:
            records = read_records(args.path)
        except ValueError as e:
            print(f"health_score: --check-determinism: {e}", file=sys.stderr)
            return 2
        dark = read_records(args.dark_checks) if args.dark_checks else []
        ablation = read_records(args.ablation_records) if args.ablation_records else []
        out1 = render_markdown(compute_scorecard(records + dark), ablation_records=ablation)
        out2 = render_markdown(compute_scorecard(records + dark), ablation_records=ablation)
        if out1 != out2:
            print("health_score: --check-determinism: two runs at the same input produced different output", file=sys.stderr)
            return 1
        print("health_score: --check-determinism: OK (byte-identical across two runs)")
        return 0

    try:
        records = read_records(args.path)
    except ValueError as e:
        print(f"health_score: {e}", file=sys.stderr)
        return 2
    if args.dark_checks:
        records += read_records(args.dark_checks)
    if not records:
        print("health_score: no check records provided", file=sys.stderr)
        return 2

    scorecard = compute_scorecard(records)

    ablation_records: list[dict] = []
    if args.ablation_records:
        try:
            ablation_records = read_records(args.ablation_records)
        except ValueError as e:
            print(f"health_score: --ablation-records: {e}", file=sys.stderr)
            return 2

    if args.history:
        append_history_row(scorecard)

    if args.format == "json":
        out = dict(scorecard)
        if ablation_records:
            out["ablation_records"] = ablation_records
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(render_markdown(scorecard, ablation_records=ablation_records), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
