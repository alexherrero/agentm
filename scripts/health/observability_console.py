#!/usr/bin/env python3
"""observability_console.py — the Autonomy arc's local static-HTML dashboard
over the observability ledger's SQLite rollup (PLAN-observability-console
task 1, `wiki/designs/agentm-autonomy.md`'s "The console" section).

Reads the rollup `scripts/runner/aggregator.py` builds (tables `by_plan` /
`by_task` / `by_model` / `by_window`) and renders spend by plan/task/model
tier, the current window's utilization, and cost-per-plan (this harness's
worktree-native flow maps one completed plan to one merged PR, so cost-per-
plan is the honest, derivable stand-in for "cost per merged PR" — there is
no join between spend and actual GitHub PR-merge events).

Deterministic: identical rollup content -> byte-identical HTML, the same
`--check-determinism` idiom `scripts/health/health_score.py` already uses.
No network calls, no AI in the read/render path.

Stdlib only (no third-party deps — this must run everywhere check-all.sh
runs).

Usage:
    python3 scripts/health/observability_console.py --db-path PATH [--output PATH]
                                                       [--budget-config PATH]
                                                       [--check-determinism]
Exit:
    0  page rendered (or determinism check passed)
    1  determinism check failed
    2  setup error (missing/unreadable rollup)
"""
from __future__ import annotations

import argparse
import html
import sqlite3
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a repo-wide dependency already
    yaml = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def read_rollup(db_path: "str | Path") -> dict:
    """Read every row from the rollup's four tables. Raises `ValueError` if
    the db file or an expected table is missing — a setup error the caller
    should surface, not swallow (mirrors `health_score.read_records`)."""
    p = Path(db_path)
    if not p.is_file():
        raise ValueError(f"rollup not found at {p}")
    conn = sqlite3.connect(str(p))
    try:
        out = {}
        for table, cols in (
            ("by_plan", ("plan", "cost_usd", "event_count")),
            ("by_task", ("plan", "task", "cost_usd", "event_count")),
            ("by_model", ("model", "cost_usd", "event_count")),
            ("by_window", ("window_start", "cost_usd", "event_count")),
        ):
            try:
                rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            except sqlite3.OperationalError as e:
                raise ValueError(f"rollup missing table {table!r}: {e}") from e
            out[table] = [dict(zip(cols, r)) for r in rows]
        return out
    finally:
        conn.close()


def _read_window_ceiling(budget_config: "str | Path | None") -> "float | None":
    """`window_usd_ceiling` from a budget config (mirrors `runner.cycle.
    _read_daily_ceiling`'s own optional-config degrade: missing config or
    missing key -> None, never an error)."""
    if budget_config is None or yaml is None:
        return None
    p = Path(budget_config)
    if not p.is_file():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    ceiling = data.get("window_usd_ceiling") if isinstance(data, dict) else None
    return float(ceiling) if ceiling is not None else None


def compute_console_data(db_path: "str | Path", *, budget_config: "str | Path | None" = None) -> dict:
    """Aggregate the rollup into the console's render-ready shape. Pure
    (given the same rollup content, always the same output) — no wall-clock
    reads beyond what's already baked into the rollup's own rows."""
    rollup = read_rollup(db_path)

    by_plan = sorted(rollup["by_plan"], key=lambda r: r["plan"])
    by_model = sorted(rollup["by_model"], key=lambda r: r["model"])
    by_task = sorted(rollup["by_task"], key=lambda r: (r["plan"], r["task"]))
    by_window = sorted(rollup["by_window"], key=lambda r: r["window_start"])

    total_spend = sum(r["cost_usd"] for r in by_plan)
    plan_count = len(by_plan)
    cost_per_plan = (total_spend / plan_count) if plan_count else 0.0

    current_window = by_window[-1] if by_window else None
    window_ceiling = _read_window_ceiling(budget_config)
    window_utilization_pct = None
    if current_window is not None and window_ceiling is not None and window_ceiling > 0:
        window_utilization_pct = round(100.0 * current_window["cost_usd"] / window_ceiling, 2)

    return {
        "by_plan": by_plan,
        "by_model": by_model,
        "by_task": by_task,
        "by_window": by_window,
        "total_spend_usd": round(total_spend, 6),
        "plan_count": plan_count,
        "cost_per_plan_usd": round(cost_per_plan, 6),
        "current_window": current_window,
        "window_ceiling_usd": window_ceiling,
        "window_utilization_pct": window_utilization_pct,
    }


def _table_rows(rows: list[dict], key_col: str, key_label: str) -> str:
    if not rows:
        return f'<tr><td colspan="3" class="empty">no data yet</td></tr>'
    out = []
    for r in rows:
        out.append(
            f"<tr><td>{html.escape(str(r[key_col]))}</td>"
            f"<td>${r['cost_usd']:.4f}</td><td>{r['event_count']}</td></tr>"
        )
    return "\n".join(out)


def render_html(data: dict) -> str:
    """Render the console as a single self-contained static HTML page.
    Deterministic given `data` — no timestamps, no randomness."""
    window = data["current_window"]
    if window is not None:
        util = data["window_utilization_pct"]
        util_line = (
            f"${window['cost_usd']:.4f} spent"
            + (f" ({util:.1f}% of ${data['window_ceiling_usd']:.2f} ceiling)" if util is not None else "")
            + f" — window started {html.escape(window['window_start'])}"
        )
    else:
        util_line = "no window data yet"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>AgentM Observability Console</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2em auto; color: #222; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ font-size: 1.1em; margin-top: 2em; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.5em; }}
  th, td {{ text-align: left; padding: 0.3em 0.6em; border-bottom: 1px solid #ddd; }}
  th {{ background: #f5f5f5; }}
  .empty {{ color: #888; font-style: italic; }}
  .headline {{ font-size: 1.2em; margin: 1em 0; }}
</style>
</head>
<body>
<h1>AgentM Observability Console</h1>

<div class="headline">
  Total spend: <strong>${data['total_spend_usd']:.4f}</strong> across {data['plan_count']} plan(s)
  — <strong>${data['cost_per_plan_usd']:.4f}</strong>/plan (this harness's one-plan-one-PR convention:
  the honest stand-in for cost-per-merged-PR — no direct spend↔merge join exists)
</div>

<div class="headline">Current window: {util_line}</div>

<h2>Spend by plan</h2>
<table>
<tr><th>Plan</th><th>Cost</th><th>Events</th></tr>
{_table_rows(data['by_plan'], 'plan', 'Plan')}
</table>

<h2>Spend by model tier</h2>
<table>
<tr><th>Model</th><th>Cost</th><th>Events</th></tr>
{_table_rows(data['by_model'], 'model', 'Model')}
</table>

<h2>Spend by task</h2>
<table>
<tr><th>Plan / Task</th><th>Cost</th><th>Events</th></tr>
{"".join(f'<tr><td>{html.escape(r["plan"])} / {html.escape(r["task"])}</td><td>${r["cost_usd"]:.4f}</td><td>{r["event_count"]}</td></tr>' for r in data["by_task"]) or '<tr><td colspan="3" class="empty">no data yet</td></tr>'}
</table>

<h2>Five-hour windows</h2>
<table>
<tr><th>Window start</th><th>Cost</th><th>Events</th></tr>
{"".join(f'<tr><td>{html.escape(r["window_start"])}</td><td>${r["cost_usd"]:.4f}</td><td>{r["event_count"]}</td></tr>' for r in data["by_window"]) or '<tr><td colspan="3" class="empty">no data yet</td></tr>'}
</table>

</body>
</html>
"""


def default_output_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "console.html"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Render the observability console over the ledger rollup.")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--budget-config", default=None)
    ap.add_argument("--output", default=str(default_output_path()))
    ap.add_argument("--check-determinism", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        data = compute_console_data(args.db_path, budget_config=args.budget_config)
    except ValueError as e:
        print(f"observability_console: {e}", file=sys.stderr)
        return 2

    if args.check_determinism:
        out1 = render_html(data)
        out2 = render_html(data)
        if out1 != out2:
            print("observability_console: --check-determinism: two runs at the same input produced different output", file=sys.stderr)
            return 1
        print("observability_console: --check-determinism: OK (byte-identical across two runs)")
        return 0

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(data), encoding="utf-8")
    print(f"observability_console: wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
