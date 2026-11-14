#!/usr/bin/env python3
"""The history a machine report keeps beside its one page.

A nightly report used to be a new dated note every run: seventy health
scorecards, sixty morning notes, each a near-copy of the one before, each
indexed, embedded and ranked against the notes a person wrote. A report now
keeps one page that is rewritten in place (`latest_<name>.md`), and what the
earlier runs said goes here, to `history.jsonl` in the same folder.

It is a data file on purpose. The index walks `.md` files and nothing else, so
a `.jsonl` file is committed and synced with the vault and never indexed,
embedded, linted or listed in a map. The record is kept; search does not serve
it.

One row per report per day. A second run on the same day replaces that day's
row, the way the second run used to overwrite that day's dated note.

Rows are JSON objects, one to a line, oldest first:

    {"date": "2026-11-13", "at": "2026-11-13T10:02:11Z",
     "report": "health-scorecard", ...the report's own fields..., "body": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

HISTORY_NAME = "history.jsonl"


def history_path(folder) -> Path:
    return Path(folder) / HISTORY_NAME


def _parse(line: str) -> Optional[dict]:
    """One row, or None for a line that is not one.

    A torn last line is what a crash mid-write leaves. It is skipped, never
    raised on: a history that cannot be read back would stop the report that
    appends to it.
    """
    line = line.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) and row.get("date") else None


def read_rows(folder, report: str = "") -> list:
    """Every row in the folder's history, oldest first.

    With `report`, only that report's rows: a folder shared by several reports
    (the digests' cadences) keeps them in one file.
    """
    path = history_path(folder)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = [r for r in (_parse(line) for line in text.splitlines()) if r]
    if report:
        rows = [r for r in rows if r.get("report") == report]
    rows.sort(key=lambda r: (str(r.get("date")), str(r.get("at") or "")))
    return rows


def latest_row(folder, report: str = "", before: str = "") -> Optional[dict]:
    """The newest row, or the newest dated strictly before `before`."""
    rows = read_rows(folder, report)
    if before:
        rows = [r for r in rows if str(r.get("date")) < before]
    return rows[-1] if rows else None


def _write(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(folder, report: str, date: str, fields: dict, *,
           now: Optional[datetime] = None, keep_existing: bool = False) -> dict:
    """Write one day's row for one report, replacing that day's earlier row.

    `keep_existing` is the backfill's mode: a day that already has a row is
    left as it is, so filling the history from old dated notes can never
    overwrite what a real run recorded.

    The whole file is rewritten through a temporary file and a rename, so a
    reader sees the old history or the new one and never half of either. A
    torn line left by an earlier crash is dropped by the rewrite.
    """
    now = now or datetime.now(timezone.utc)
    row = dict(fields)
    row["date"] = date
    row["report"] = report
    row.setdefault("at", now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    rows = read_rows(folder)
    same = [r for r in rows if r.get("date") == date and r.get("report") == report]
    if same and keep_existing:
        return same[-1]
    rows = [r for r in rows if not (r.get("date") == date and r.get("report") == report)]
    rows.append(row)
    rows.sort(key=lambda r: (str(r.get("date")), str(r.get("at") or "")))
    _write(history_path(folder), rows)
    return row


# ── the one-time backfill ───────────────────────────────────────────────────

NIGHT_LOG_DIR = Path("diagnostics") / "dreaming"
NIGHT_LOG_REPORT = "dreaming-night"


def backfill_night_log(memory_root) -> tuple:
    """Copy the night's old calendar notes (`calendar/YYYY/YYYY-MM-DD-dreaming.md`,
    written until task 201) into the night log's history, in the row shape
    the dreaming binary writes. Returns (rows added, notes read); the notes
    are read and never changed."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import calendar_facets

    root = Path(memory_root)
    calendar = calendar_facets.calendar_root(root)
    if calendar is None:
        return 0, 0
    folder = root / NIGHT_LOG_DIR
    have = {r["date"] for r in read_rows(folder, NIGHT_LOG_REPORT)}
    added = read = 0
    for note in sorted(calendar.glob("[0-9][0-9][0-9][0-9]/????-??-??-dreaming.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        read += 1
        day = note.name[:10]
        if day in have:
            continue
        body = text
        if text.startswith("---\n") and "\n---\n" in text[4:]:
            body = text[4:].split("\n---\n", 1)[1].lstrip("\n")
        acts = sum(1 for line in body.splitlines() if line.startswith("- "))
        record(folder, NIGHT_LOG_REPORT, day,
               {"acts": acts, "body": f"# What the night did — {day}\n\n{body}",
                "at": f"{day}T00:00:00Z", "backfilled_from": note.name}, keep_existing=True)
        added += 1
    return added, read


def backfill(memory_root) -> list:
    """Fill each report's history from the dated notes still on disk. Returns
    one (report, rows added, notes read) triple per report.

    Each report's own module knows how to read its old notes; this only calls
    them in turn. The notes are read and never changed, and a second run adds
    nothing, so this is safe to run before the dated notes are cleared out and
    again whenever one turns up.
    """
    here = Path(__file__).resolve().parent
    for extra in (here, here.parents[3] / "scripts" / "health"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import corpus_scorecard
    import inbox_digest
    import morning_note
    import vault_lint

    root = Path(memory_root)
    night = backfill_night_log(root)
    return [
        ("night log", *night),
        ("health scorecard", *corpus_scorecard.backfill_history(root / corpus_scorecard.DIAGNOSTICS_DIR)),
        ("morning note", *morning_note.backfill_history(root / morning_note.DIAGNOSTICS_DIR)),
        ("digests", *inbox_digest.backfill_history(root)),
        ("lint report", *vault_lint.backfill_history(root / "diagnostics" / "lint")),
    ]


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fill each machine report's history.jsonl from the dated notes on disk. "
                    "Reads the notes; changes none of them.")
    ap.add_argument("command", choices=("backfill",))
    ap.add_argument("--vault-path", default=None, help="the memory root (overrides MEMORY_ROOT)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = args.vault_path or os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH")
    if not root:
        print("report-history: no memory root. Pass --vault-path or set $MEMORY_ROOT.", file=sys.stderr)
        return 2
    if not Path(root).is_dir():
        print(f"report-history: not a directory: {root}", file=sys.stderr)
        return 2
    for report, added, read in backfill(root):
        print(f"report-history: {report}: read {read} dated notes, added {added} rows")
    print("report-history: no note was changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
