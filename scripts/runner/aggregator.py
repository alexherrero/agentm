"""Observability ledger aggregator (PLAN-observability-ledger, agentm half,
task 1) -- a scheduled runner job that folds the raw telemetry event log
crickets' token-audit plugin writes into a small, derived SQLite rollup with
per-plan, per-task, per-model, and per-window tables (agentm-autonomy.md's
Design section: "A scheduled runner job folds the raw events into a small
SQLite file with per-plan, per-task, per-model, and per-window tables").

The event log is the source of truth; this rollup is fully rebuilt from it
on every run (delete + recreate, never an incremental upsert) -- so
rebuilding twice from the same events produces an identical rollup, the
design's own idempotency requirement ("if the aggregation logic changes,
the rollup rebuilds from the events and loses nothing").

Window bucketing reuses crickets' `analyzer._compute_windows()` (the same
five-hour billing-window logic the token-audit capability already has) via
the same sibling-clone resolution convention crickets' own
`session_cost_writer.py` uses for agentm's `save.py` -- resolved, never
vendor-copied. Unlike that Stop-hook capture (which must never block a
session close), this is a scheduled background job: an unresolvable
crickets sibling is a loud, non-zero-exit failure, not a silent no-op --
the runner's own per-job outcome capture (`cycle._run_one`) already isolates
one job's failure from the rest of the cycle.

Only single-device merging exists today (every event log file under the
telemetry dir is read as one corpus) -- multi-machine support is designed,
not live, per the schema's own `device`/`schema_version` fields.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

_CRICKETS_ANALYZER_REL = Path("src") / "tokens" / "scripts"

_analyzer_module = None
_analyzer_loaded = False


def _candidate_analyzer_dirs() -> list[Path]:
    candidates = []
    env_dir = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)))
    candidates.append(Path.home() / "Antigravity" / "crickets" / _CRICKETS_ANALYZER_REL)
    return candidates


def _find_analyzer_dir() -> "Path | None":
    for candidate in _candidate_analyzer_dirs():
        if (candidate / "analyzer.py").is_file():
            return candidate
    return None


def load_analyzer_module():
    """Return crickets' analyzer module, loaded once and cached. None if
    crickets is unresolvable (a loud failure at the call site, not here --
    this function itself is graceful-return, matching the sibling-clone
    resolution shape `session_cost_writer.load_save_module()` established)."""
    global _analyzer_module, _analyzer_loaded
    if _analyzer_loaded:
        return _analyzer_module
    _analyzer_loaded = True
    d = _find_analyzer_dir()
    if d is None:
        _analyzer_module = None
        return None
    spec = importlib.util.spec_from_file_location("crickets_analyzer_bridge_aggregator", d / "analyzer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crickets_analyzer_bridge_aggregator"] = module
    spec.loader.exec_module(module)
    _analyzer_module = module
    return module


def _reset_cache_for_tests() -> None:
    global _analyzer_module, _analyzer_loaded
    _analyzer_module = None
    _analyzer_loaded = False


def default_telemetry_dir() -> Path:
    """`$AGENTM_TELEMETRY_DIR` override, else `~/.agentm/telemetry/` -- the
    same default + override convention crickets' `event_log.py` uses on the
    write side."""
    env = os.environ.get("AGENTM_TELEMETRY_DIR", "").strip()
    return Path(env) if env else Path.home() / ".agentm" / "telemetry"


def load_events(telemetry_dir: "str | Path | None" = None) -> list[dict]:
    """Read every event line from every `events-*.jsonl` file under
    `telemetry_dir` (default: `default_telemetry_dir()`). Malformed lines
    and a missing directory are skipped gracefully, never raised.
    """
    root = Path(telemetry_dir) if telemetry_dir is not None else default_telemetry_dir()
    if not root.is_dir():
        return []
    events: list[dict] = []
    for path in sorted(root.glob("events-*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                events.append(rec)
    return events


def _windows_for(events: list[dict], analyzer) -> list:
    """Bucket `events` (already sorted by `ts`) into five-hour windows via
    crickets' `analyzer._compute_windows()`, adapting each event into the
    lightweight `MessageRecord` shape that function expects."""
    records = [
        analyzer.MessageRecord(
            timestamp=e.get("ts", ""),
            model=e.get("model") or "unknown",
            input_tokens=0, cache_write_tokens=0, cache_read_tokens=0, output_tokens=0,
            cost_usd=float(e.get("cost_usd") or 0.0),
            is_floor=False,
        )
        for e in events
    ]
    return analyzer._compute_windows(records)


def build_rollup(events: list[dict], db_path: "str | Path", *, analyzer=None) -> None:
    """Fully rebuild the SQLite rollup at `db_path` from `events`. Always a
    from-scratch rebuild (delete + recreate) -- never an incremental
    upsert -- so two calls with the same events produce an identical
    rollup. Raises `RuntimeError` if crickets' analyzer can't be resolved
    (no silent, mathematically-wrong window bucketing).
    """
    analyzer = analyzer if analyzer is not None else load_analyzer_module()
    if analyzer is None:
        raise RuntimeError(
            "aggregator: crickets sibling checkout unresolvable -- cannot reuse "
            "analyzer._compute_windows(). Set $CRICKETS_SCRIPTS_DIR or clone "
            "crickets as a sibling of agentm (~/Antigravity/crickets)."
        )

    session_cost = [e for e in events if e.get("event") == "session-cost"]
    ordered = sorted(session_cost, key=lambda e: (e.get("ts") or "", e.get("model") or ""))

    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE by_plan (
                plan TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL
            );
            CREATE TABLE by_task (
                plan TEXT NOT NULL, task TEXT NOT NULL,
                cost_usd REAL NOT NULL, event_count INTEGER NOT NULL,
                PRIMARY KEY (plan, task)
            );
            CREATE TABLE by_model (
                model TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL
            );
            CREATE TABLE by_window (
                window_start TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL
            );
            """
        )

        by_plan: dict[str, list] = {}
        by_task: dict[tuple, list] = {}
        by_model: dict[str, list] = {}

        for e in ordered:
            tags = e.get("tags") or {}
            plan = tags.get("plan")
            task = tags.get("task")
            model = e.get("model") or "unknown"
            cost = float(e.get("cost_usd") or 0.0)

            if plan:
                agg = by_plan.setdefault(plan, [0.0, 0])
                agg[0] += cost
                agg[1] += 1
                if task:
                    tagg = by_task.setdefault((plan, task), [0.0, 0])
                    tagg[0] += cost
                    tagg[1] += 1

            magg = by_model.setdefault(model, [0.0, 0])
            magg[0] += cost
            magg[1] += 1

        for plan in sorted(by_plan):
            cost, count = by_plan[plan]
            conn.execute("INSERT INTO by_plan VALUES (?, ?, ?)", (plan, cost, count))
        for plan, task in sorted(by_task):
            cost, count = by_task[(plan, task)]
            conn.execute("INSERT INTO by_task VALUES (?, ?, ?, ?)", (plan, task, cost, count))
        for model in sorted(by_model):
            cost, count = by_model[model]
            conn.execute("INSERT INTO by_model VALUES (?, ?, ?)", (model, cost, count))

        for w in _windows_for(ordered, analyzer):
            conn.execute(
                "INSERT INTO by_window VALUES (?, ?, ?)",
                (w.start_ts, w.total_cost_usd, w.message_count),
            )

        conn.commit()
    finally:
        conn.close()


def default_db_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "rollup.db"


def main(argv: "list[str] | None" = None) -> int:
    """Runner-job / CLI entry point. Prints `{"total_cost_usd": 0.0}` as its
    final stdout line -- the aggregator spends nothing itself (a local
    read+write pass, not a dispatched agent) -- so the runner's cost-parsing
    convention (`cycle._parse_reported_cost`) has a well-formed line to read
    even though it's always zero."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument("--db-path", default=str(default_db_path()))
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    events = load_events(args.telemetry_dir)
    try:
        build_rollup(events, args.db_path)
    except RuntimeError as e:
        print(f"aggregator: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"total_cost_usd": 0.0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
