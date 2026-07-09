#!/usr/bin/env python3
"""verify-efficiency.py — the `efficiency` health-family's first live checks
(AA5 consolidation task C3, AA5-REENTRY-VERDICT.md §2 item 4 + §1 D4).

Every check here runs against SYNTHETIC fixtures, never real telemetry --
this suite must run hermetically in CI, and this repo's CI never checks out
a crickets sibling (confirmed: no crickets checkout step in
tests-linux.yml / tests-mac.yml / health-nightly.yml), so
`aggregator.build_rollup()` is always called with an injected fake analyzer
(mirroring `scripts/test_aggregator.py`'s `_FakeAnalyzer`), never
`analyzer=None`. Because every check's input is synthetic and fully known,
every record this suite emits is live (`pass: true/false`) -- never dark.
The one genuinely data-conditioned efficiency check (does this MACHINE's
real `~/.agentm/telemetry` produce a sane rollup) lives in
`run-heavy-tier.sh`'s check 7, not here, and goes honest-dark on zero real
events per the AA4 pattern.

Checks (four groups the AA5 ratification named): aggregator rebuild-
idempotency, rollup arithmetic, console render determinism + arithmetic,
digest-ladder cadence math at the three windowed lookbacks
(`agentm-autonomy.md`'s Digests table: daily 24h, 3day 72h, weekly 168h).

Usage:   python3 scripts/health/verify-efficiency.py
         python3 scripts/health/verify-efficiency.py --jsonl-out records.jsonl
Exit:    0 iff every check passes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS_DIR))

from runner import aggregator  # noqa: E402
import observability_console as oc  # noqa: E402
import inbox_digest as idg  # noqa: E402

# ── synthetic fake analyzer (mirrors scripts/test_aggregator.py) ───────────


@dataclass
class _FakeMessageRecord:
    timestamp: str
    model: str
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    is_floor: bool


@dataclass
class _FakeWindowSummary:
    start_ts: str
    message_count: int
    total_cost_usd: float


class _FakeAnalyzer:
    """Real five-hour bucketing logic, copied verbatim from
    analyzer._compute_windows for test hermeticity -- never a network or
    crickets-sibling dependency."""

    MessageRecord = _FakeMessageRecord
    _WINDOW = timedelta(hours=5)

    @staticmethod
    def _parse_ts(ts):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _compute_windows(cls, messages):
        if not messages:
            return []
        windows = []
        win_start_ts = messages[0].timestamp
        win_start_dt = cls._parse_ts(win_start_ts)
        win_cost = 0.0
        win_count = 0
        for msg in messages:
            dt = cls._parse_ts(msg.timestamp)
            if win_start_dt is not None and dt is not None and dt - win_start_dt >= cls._WINDOW:
                windows.append(_FakeWindowSummary(win_start_ts, win_count, win_cost))
                win_start_ts = msg.timestamp
                win_start_dt = dt
                win_cost = 0.0
                win_count = 0
            win_cost += msg.cost_usd
            win_count += 1
        if win_count:
            windows.append(_FakeWindowSummary(win_start_ts, win_count, win_cost))
        return windows


_ANALYZER = _FakeAnalyzer()


def _event(ts, model, cost, *, plan, task):
    return {
        "ts": ts, "schema_version": 1, "device": "verify-efficiency", "session_id": "s1",
        "parent_id": None, "event": "session-cost", "model": model,
        "tokens_by_kind": {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0},
        "cost_usd": cost, "tags": {"plan": plan, "task": task, "arc": None, "grade": None},
    }


# Fixed synthetic corpus: two plans, three models, spanning two 5h windows.
_EVENTS = [
    _event("2026-07-07T00:00:00Z", "claude-sonnet-5", 1.5, plan="p1", task="1"),
    _event("2026-07-07T01:00:00Z", "claude-sonnet-5", 2.5, plan="p1", task="2"),
    _event("2026-07-07T02:00:00Z", "claude-opus-4-8", 4.0, plan="p2", task="1"),
    _event("2026-07-07T06:00:00Z", "claude-haiku-4-5", 0.5, plan="p2", task="1"),
]

_EXPECTED_BY_PLAN = {"p1": (4.0, 2), "p2": (4.5, 2)}
_EXPECTED_BY_MODEL = {"claude-sonnet-5": (4.0, 2), "claude-opus-4-8": (4.0, 1), "claude-haiku-4-5": (0.5, 1)}
_EXPECTED_TOTAL = 8.5
_EXPECTED_COST_PER_PLAN = _EXPECTED_TOTAL / 2

# R1.8-style JSONL check-record emission (health scorecard) — no-ops unless
# --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE = "verify-efficiency"
HEALTH_AXIS = "efficiency"
JSONL_OUT = os.environ.get("HEALTH_JSONL_OUT") or None
if "--jsonl-out" in sys.argv:
    JSONL_OUT = sys.argv[sys.argv.index("--jsonl-out") + 1]

PASS = 0
FAIL = 0
RESULTS: list[str] = []


def _emit_jsonl_check(desc: str, passed: bool) -> None:
    if not JSONL_OUT:
        return
    record = {"suite": HEALTH_SUITE, "axis": HEALTH_AXIS, "check": desc, "pass": passed, "weight": 1.0}
    with open(JSONL_OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def ok(desc: str) -> None:
    global PASS
    RESULTS.append(f"  PASS  {desc}")
    PASS += 1
    _emit_jsonl_check(desc, True)


def bad(desc: str, detail: str) -> None:
    global FAIL
    RESULTS.append(f"  FAIL  {desc}\n          ↳ {detail}")
    FAIL += 1
    _emit_jsonl_check(desc, False)


def _dump_rollup(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        out = {}
        for table, cols in (
            ("by_plan", ("plan", "cost_usd", "event_count")),
            ("by_task", ("plan", "task", "cost_usd", "event_count")),
            ("by_model", ("model", "cost_usd", "event_count")),
            ("by_window", ("window_start", "cost_usd", "event_count")),
        ):
            rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {cols[0]}").fetchall()
            out[table] = rows
        return out
    finally:
        conn.close()


def check_idempotency_and_arithmetic(tmp: Path) -> Path:
    """Group 1 + 2: build the rollup twice from the same synthetic events and
    assert both an identical rebuild (idempotency) and hand-computed
    arithmetic (by_plan/by_task/by_model sums)."""
    db1 = tmp / "rollup1.db"
    db2 = tmp / "rollup2.db"
    aggregator.build_rollup(_EVENTS, db1, analyzer=_ANALYZER)
    aggregator.build_rollup(_EVENTS, db2, analyzer=_ANALYZER)

    dump1, dump2 = _dump_rollup(db1), _dump_rollup(db2)
    if dump1 == dump2:
        ok("aggregator rebuild is idempotent (same synthetic events -> identical rollup across two builds)")
    else:
        bad(
            "aggregator rebuild is idempotent (same synthetic events -> identical rollup across two builds)",
            f"rollups differ: {dump1} != {dump2}",
        )

    by_plan = {row[0]: (row[1], row[2]) for row in dump1["by_plan"]}
    by_model = {row[0]: (row[1], row[2]) for row in dump1["by_model"]}
    if by_plan == _EXPECTED_BY_PLAN and by_model == _EXPECTED_BY_MODEL:
        ok("rollup arithmetic: by_plan/by_model sums match hand-computed totals from synthetic events")
    else:
        bad(
            "rollup arithmetic: by_plan/by_model sums match hand-computed totals from synthetic events",
            f"by_plan={by_plan!r} (want {_EXPECTED_BY_PLAN!r}); by_model={by_model!r} (want {_EXPECTED_BY_MODEL!r})",
        )
    return db1


def check_console(db_path: Path) -> None:
    """Group 3: console render determinism + arithmetic over the synthetic rollup."""
    data = oc.compute_console_data(db_path)
    render1 = oc.render_html(data)
    render2 = oc.render_html(data)
    if render1 == render2:
        ok("console render is deterministic (byte-identical across two renders of the same rollup)")
    else:
        bad("console render is deterministic (byte-identical across two renders of the same rollup)", "renders differ")

    total_ok = abs(data["total_spend_usd"] - _EXPECTED_TOTAL) < 1e-9
    per_plan_ok = abs(data["cost_per_plan_usd"] - _EXPECTED_COST_PER_PLAN) < 1e-9
    if total_ok and per_plan_ok:
        ok("console arithmetic: total_spend_usd/cost_per_plan_usd match hand-computed values")
    else:
        bad(
            "console arithmetic: total_spend_usd/cost_per_plan_usd match hand-computed values",
            f"total_spend_usd={data['total_spend_usd']!r} (want {_EXPECTED_TOTAL!r}); "
            f"cost_per_plan_usd={data['cost_per_plan_usd']!r} (want {_EXPECTED_COST_PER_PLAN!r})",
        )


# Digest-ladder lookbacks, locked in agentm-autonomy.md's Digests table.
_CADENCE_CASES = (
    ("daily", 86400, 23 * 3600, 25 * 3600),
    ("3day", 3 * 86400, 71 * 3600, 73 * 3600),
    ("weekly", 7 * 86400, 167 * 3600, 169 * 3600),
)
_NOW = datetime(2026, 7, 8, 0, 0, 0, tzinfo=timezone.utc)


def check_digest_cadence_math() -> None:
    """Group 4: cadence-boundary math at each locked lookback -- a row just
    inside the window is summed in, a row just outside is excluded."""
    for cadence, lookback_seconds, inside_age, outside_age in _CADENCE_CASES:
        inside_ts = (_NOW - timedelta(seconds=inside_age)).strftime("%Y-%m-%dT%H:%M:%SZ")
        outside_ts = (_NOW - timedelta(seconds=outside_age)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [
            {"window_start": inside_ts, "cost_usd": 3.0, "event_count": 2},
            {"window_start": outside_ts, "cost_usd": 999.0, "event_count": 999},
        ]
        slice_data = idg.compute_window_slice(rows, now=_NOW, lookback_seconds=lookback_seconds)
        desc = f"digest cadence math ({cadence}, {lookback_seconds}s lookback): includes the in-window row, excludes the out-of-window row"
        if slice_data["cost_usd"] == 3.0 and slice_data["event_count"] == 2 and slice_data["window_count"] == 1:
            ok(desc)
        else:
            bad(desc, f"got {slice_data!r} (want cost_usd=3.0, event_count=2, window_count=1)")


def main(argv: "list[str] | None" = None) -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db1 = check_idempotency_and_arithmetic(tmp)
        check_console(db1)
    check_digest_cadence_math()

    print(f"verify-efficiency: {PASS} passed, {FAIL} failed", file=sys.stderr)
    for line in RESULTS:
        print(line, file=sys.stderr)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
