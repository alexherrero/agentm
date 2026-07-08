#!/usr/bin/env python3
"""inbox_digest.py — the observability ledger's four-cadence digest ladder
(PLAN-observability-console task 2, `wiki/designs/agentm-autonomy.md`'s
"Digests" section).

Each cadence reads the rollup's `by_window` table at its own lookback,
writes a note into the vault's `_inbox/` (the B1-ratified contract:
`personal/_inbox/<slug>.md`, minimal frontmatter, direct file write --
mirrors `harness/skills/memory/scripts/reflect.py`'s `_save_candidate_to_
inbox()`), and appends a row to a local digest-history ledger so the
monthly cadence can report a trend across the shorter horizons without
re-deriving it from the (already-flat) rollup.

Cadence -> lookback:
  daily    24h   -- spend + run summary
  3day     72h   -- 3-day rollup
  weekly   168h  -- weekly rollup
  monthly  --    -- trend across the digest-history ledger's last 30 days

Idempotent per day: a same-day rerun of the same cadence returns the
already-written note's path without creating a second file or re-appending
a duplicate history row for that (cadence, date) pair.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

_CADENCE_LOOKBACK_SECONDS = {
    "daily": 86400,
    "3day": 3 * 86400,
    "weekly": 7 * 86400,
}
_CADENCE_LABELS = {
    "daily": "spend and run summary",
    "3day": "3-day rollup",
    "weekly": "weekly rollup",
    "monthly": "trends across all of the above",
}


def default_history_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "digest-history.jsonl"


def _parse_ts(ts: str) -> "datetime | None":
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_window_slice(by_window_rows: list[dict], *, now: datetime, lookback_seconds: int) -> dict:
    """Sum `by_window` rows whose `window_start` falls within `lookback_seconds`
    of `now`. Rows with an unparseable timestamp are skipped, never raised."""
    cutoff = now - timedelta(seconds=lookback_seconds)
    cost = 0.0
    events = 0
    windows = 0
    for row in by_window_rows:
        dt = _parse_ts(row.get("window_start", ""))
        if dt is None or dt < cutoff:
            continue
        cost += row["cost_usd"]
        events += row["event_count"]
        windows += 1
    return {"cost_usd": round(cost, 6), "event_count": events, "window_count": windows}


def append_digest_history(cadence: str, slice_data: dict, *, now: datetime,
                           history_path: "Path | None" = None) -> dict:
    """Append one row to the digest-history ledger. Idempotent per (cadence,
    date): if a row for this cadence+date already exists, it is not
    duplicated -- the existing row is returned unchanged."""
    path = history_path if history_path is not None else default_history_path()
    date = now.strftime("%Y-%m-%d")

    existing = read_all_history(path)
    for row in existing:
        if row.get("cadence") == cadence and row.get("date") == date:
            return row

    row = {
        "cadence": cadence, "date": date, "ts": now.isoformat(),
        "cost_usd": slice_data["cost_usd"], "event_count": slice_data["event_count"],
        "window_count": slice_data["window_count"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_all_history(history_path: "Path | None" = None) -> list[dict]:
    path = history_path if history_path is not None else default_history_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def read_recent_history(history_path: "Path | None", *, now: datetime, lookback_seconds: int) -> list[dict]:
    cutoff = now - timedelta(seconds=lookback_seconds)
    out = []
    for row in read_all_history(history_path):
        dt = _parse_ts(row.get("ts", ""))
        if dt is not None and dt >= cutoff:
            out.append(row)
    return sorted(out, key=lambda r: r["ts"])


def render_digest_body(cadence: str, slice_data: "dict | None", *, now: datetime,
                        trend_rows: "list[dict] | None" = None) -> str:
    """Render the digest note's markdown body. Deterministic given its inputs."""
    label = _CADENCE_LABELS[cadence]
    lines = [f"# Observability digest — {cadence} ({label})", ""]
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    if cadence == "monthly":
        rows = trend_rows or []
        if not rows:
            lines.append("No digest history recorded yet this month.")
        else:
            lines.append("| Date | Cadence | Cost | Events |")
            lines.append("|---|---|---:|---:|")
            for r in rows:
                lines.append(f"| {r['date']} | {r['cadence']} | ${r['cost_usd']:.4f} | {r['event_count']} |")
            total = sum(r["cost_usd"] for r in rows)
            lines.append("")
            lines.append(f"**Total across recorded digests this window: ${total:.4f}**")
    else:
        assert slice_data is not None
        lines.append(f"- Spend: ${slice_data['cost_usd']:.4f}")
        lines.append(f"- Events: {slice_data['event_count']}")
        lines.append(f"- 5h windows in range: {slice_data['window_count']}")

    lines.append("")
    return "\n".join(lines) + "\n"


def digest_slug(cadence: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%d')}-digest-{cadence}"


def write_digest_note(vault_path: "str | Path", cadence: str, body: str, *, now: datetime) -> "Path | None":
    """Write the digest note to `<vault>/personal/_inbox/<slug>.md` (the
    B1-ratified contract). Idempotent per day: if today's note for this
    cadence already exists, its path is returned unchanged -- never a second
    file, never a numeric-suffix collision (unlike the mining inbox's raw-
    capture convention, a digest slug is meant to be stable per day).
    Returns None if the vault directory doesn't exist (graceful-skip).
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        return None
    inbox_dir = vault / "personal" / "_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    slug = digest_slug(cadence, now)
    target = inbox_dir / f"{slug}.md"
    if target.is_file():
        return target

    fm = (
        "---\n"
        "kind: telemetry\n"
        "status: inbox\n"
        f"slug: {slug}\n"
        f"digest_cadence: {cadence}\n"
        f"digest_date: {now.strftime('%Y-%m-%d')}\n"
        "---\n"
    )
    target.write_text(fm + "\n" + body, encoding="utf-8")
    return target


def run_digest(cadence: str, db_path: "str | Path", vault_path: "str | Path", *,
               now: "datetime | None" = None, history_path: "Path | None" = None) -> "Path | None":
    """End-to-end: compute the cadence's slice (or trend, for monthly), append
    to history (non-monthly cadences), and write the vault note. Returns the
    written (or already-existing) note path, or None on graceful-skip."""
    now = now if now is not None else datetime.now(timezone.utc)

    # Local import to avoid a hard dependency at module-import time for
    # callers that only need the pure functions above (e.g. unit tests).
    sys.path.insert(0, str(HERE))
    import observability_console as oc  # noqa: E402

    try:
        rollup = oc.read_rollup(db_path)
    except ValueError:
        return None

    if cadence == "monthly":
        trend_rows = read_recent_history(history_path, now=now, lookback_seconds=30 * 86400)
        body = render_digest_body(cadence, None, now=now, trend_rows=trend_rows)
    else:
        lookback = _CADENCE_LOOKBACK_SECONDS[cadence]
        slice_data = compute_window_slice(rollup["by_window"], now=now, lookback_seconds=lookback)
        append_digest_history(cadence, slice_data, now=now, history_path=history_path)
        body = render_digest_body(cadence, slice_data, now=now)

    return write_digest_note(vault_path, cadence, body, now=now)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Write one observability digest note.")
    ap.add_argument("--cadence", required=True, choices=("daily", "3day", "weekly", "monthly"))
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--vault-path", required=True)
    ap.add_argument("--history-path", default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    history_path = Path(args.history_path) if args.history_path else None
    target = run_digest(args.cadence, args.db_path, args.vault_path, history_path=history_path)
    if target is None:
        print("inbox_digest: no-op (missing rollup or vault)", file=sys.stderr)
        return 0
    print(json.dumps({"total_cost_usd": 0.0}))
    print(f"inbox_digest: wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
