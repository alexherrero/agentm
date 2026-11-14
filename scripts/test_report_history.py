#!/usr/bin/env python3
"""The history a machine report keeps beside its one page (task 201).

A report keeps one page and one `history.jsonl`. These tests hold the history
to what the dated notes used to give for free: one record a day, the second
run of a day replacing the first, every earlier day still there, and a file
that survives a crash in the middle of a write.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import report_history as rh  # noqa: E402

AT = datetime(2026, 11, 13, 10, 2, 11, tzinfo=timezone.utc)


class ReportHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name) / "diagnostics" / "health"

    def lines(self) -> list:
        text = rh.history_path(self.folder).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def test_a_first_run_creates_the_folder_and_one_row(self):
        row = rh.record(self.folder, "health-scorecard", "2026-11-13",
                        {"body": "# one"}, now=AT)
        self.assertEqual(row["at"], "2026-11-13T10:02:11Z")
        self.assertEqual(self.lines(), [{
            "at": "2026-11-13T10:02:11Z", "body": "# one",
            "date": "2026-11-13", "report": "health-scorecard"}])

    def test_a_second_run_the_same_day_replaces_that_days_row(self):
        rh.record(self.folder, "health-scorecard", "2026-11-13", {"body": "first"}, now=AT)
        rh.record(self.folder, "health-scorecard", "2026-11-13", {"body": "second"}, now=AT)
        self.assertEqual([r["body"] for r in self.lines()], ["second"])

    def test_another_day_is_another_row_and_rows_stay_oldest_first(self):
        rh.record(self.folder, "health-scorecard", "2026-11-13", {"body": "b"}, now=AT)
        rh.record(self.folder, "health-scorecard", "2026-11-12", {"body": "a"}, now=AT)
        rh.record(self.folder, "health-scorecard", "2026-11-14", {"body": "c"}, now=AT)
        self.assertEqual([r["date"] for r in self.lines()],
                         ["2026-11-12", "2026-11-13", "2026-11-14"])
        self.assertEqual([r["body"] for r in rh.read_rows(self.folder)], ["a", "b", "c"])

    def test_two_reports_share_a_file_and_never_replace_each_other(self):
        rh.record(self.folder, "digest-weekly", "2026-11-13", {"body": "w"}, now=AT)
        rh.record(self.folder, "digest-monthly", "2026-11-13", {"body": "m"}, now=AT)
        rh.record(self.folder, "digest-weekly", "2026-11-13", {"body": "w2"}, now=AT)
        self.assertEqual(len(self.lines()), 2)
        self.assertEqual([r["body"] for r in rh.read_rows(self.folder, "digest-weekly")], ["w2"])
        self.assertEqual([r["body"] for r in rh.read_rows(self.folder, "digest-monthly")], ["m"])

    def test_the_backfill_never_overwrites_a_day_a_real_run_recorded(self):
        rh.record(self.folder, "morning-note", "2026-11-13", {"body": "real"}, now=AT)
        kept = rh.record(self.folder, "morning-note", "2026-11-13", {"body": "from the old note"},
                         now=AT, keep_existing=True)
        self.assertEqual(kept["body"], "real")
        self.assertEqual([r["body"] for r in self.lines()], ["real"])
        rh.record(self.folder, "morning-note", "2026-11-12", {"body": "older"},
                  now=AT, keep_existing=True)
        self.assertEqual([r["body"] for r in self.lines()], ["older", "real"])

    def test_a_torn_last_line_is_skipped_and_the_next_write_heals_the_file(self):
        rh.record(self.folder, "morning-note", "2026-11-12", {"body": "whole"}, now=AT)
        path = rh.history_path(self.folder)
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"date": "2026-11-13", "report": "morning-note", "bo')
        self.assertEqual([r["body"] for r in rh.read_rows(self.folder)], ["whole"])
        rh.record(self.folder, "morning-note", "2026-11-14", {"body": "next"}, now=AT)
        self.assertEqual([r["body"] for r in self.lines()], ["whole", "next"])

    def test_the_newest_row_and_the_newest_before_a_day(self):
        self.assertIsNone(rh.latest_row(self.folder))
        for day in ("2026-11-11", "2026-11-12", "2026-11-13"):
            rh.record(self.folder, "morning-note", day, {"body": day}, now=AT)
        self.assertEqual(rh.latest_row(self.folder, "morning-note")["date"], "2026-11-13")
        self.assertEqual(
            rh.latest_row(self.folder, "morning-note", before="2026-11-13")["date"],
            "2026-11-12")
        self.assertIsNone(rh.latest_row(self.folder, "morning-note", before="2026-11-11"))

    def test_the_history_is_never_a_markdown_file(self):
        # The index walks `.md` and nothing else; the name is the wall.
        rh.record(self.folder, "morning-note", "2026-11-13", {"body": "x"}, now=AT)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["history.jsonl"])
        self.assertFalse(rh.HISTORY_NAME.endswith(".md"))

    def test_no_temporary_file_is_left_behind(self):
        rh.record(self.folder, "morning-note", "2026-11-13", {"body": "x"}, now=AT)
        rh.record(self.folder, "morning-note", "2026-11-14", {"body": "y"}, now=AT)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["history.jsonl"])


class BackfillCommandTests(unittest.TestCase):
    """`report_history.py backfill`: one command fills every report's history
    from the dated notes on disk, and changes none of them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "agent"
        d = self.root / "diagnostics"
        self.notes = {
            d / "health" / "2026-11-08-health-scorecard.md": "| gold-set R@5 | 0.5833 | 35 of 60 scored |\n",
            d / "morning" / "2026-11-08.md": "---\ntitle: Morning 2026-11-08\nwindow_until: 1790000000\n---\n",
            d / "digests" / "20261108-digest-weekly.md": "---\nkind: brief\n---\n\n# digest\n",
            d / "lint" / "vault-lint-2026-11-07.md":
                "**Summary:** 1 error · 0 warn · 0 info across 5 entries (2 non-entry file(s) skipped).\n",
        }
        for path, text in self.notes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_every_report_gets_its_rows_and_no_note_changes(self):
        self.assertEqual(rh.backfill(self.root), [
            ("health scorecard", 1, 1), ("morning note", 1, 1),
            ("digests", 1, 1), ("lint report", 1, 1)])
        d = self.root / "diagnostics"
        self.assertEqual(rh.read_rows(d / "health")[0]["date"], "2026-11-08")
        self.assertEqual(rh.read_rows(d / "morning")[0]["window_until"], 1790000000)
        self.assertEqual(rh.read_rows(d / "digests")[0]["report"], "digest-weekly")
        self.assertEqual(rh.read_rows(d / "lint")[0]["errors"], 1)
        for path, text in self.notes.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text)
        # A second run adds nothing.
        self.assertEqual([added for _name, added, _read in rh.backfill(self.root)], [0, 0, 0, 0])

    def test_the_command_needs_a_memory_root_that_exists(self):
        import contextlib
        import io
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("MEMORY_ROOT", None)
            env.pop("MEMORY_VAULT_PATH", None)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rh.main(["backfill"]), 2)
                self.assertEqual(rh.main(["backfill", "--vault-path", str(self.root / "nowhere")]), 2)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(rh.main(["backfill", "--vault-path", str(self.root)]), 0)
        self.assertIn("no note was changed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
