#!/usr/bin/env python3
"""Tests for `scripts/check-tracker-schema.py` (agentm-vault plan 09, task 2).

An empty projects space is clean; a good tracker in each of the design's places
passes; a malformed tracker, one that names the wrong project or task, and one
outside a tracker's place are each named; retired projects and notes that are
not trackers are left alone; the command line's exit codes follow the findings.

Run directly:

    python3 scripts/test_check_tracker_schema.py
"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import tracker as tk  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_tracker_schema", _HERE / "check-tracker-schema.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GOOD = tk.new(title="Build it", project="demo", task="build-it",
              objective="It is built.", next_step="Start.", today="2026-09-12")


class TrackerGate(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = Path(tempfile.mkdtemp(prefix="agentm-tracker-gate-"))
        self.project = self.projects / "demo"
        self.project.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.projects, ignore_errors=True)

    def _put(self, rel: str, text: str) -> None:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _findings(self) -> tuple:
        return gate.projects_findings(self.projects)

    def test_an_empty_projects_space_is_clean(self) -> None:
        self._put("decisions/a-ruling.md", "---\ntype: convention\n---\n# A ruling\n")
        self.assertEqual(self._findings(), (0, []))

    def test_a_good_tracker_in_every_place_is_clean(self) -> None:
        self._put("tracker.md", tk.render(dataclasses.replace(GOOD, task=None)))
        self._put("tasks/build-it/tracker.md", tk.render(GOOD))
        self.assertEqual(self._findings(), (2, []))

    def test_a_tracker_in_a_returned_retired_directory_is_misplaced(self) -> None:
        # agentm-vault plan 15: the flat pair's two tracker places retired with
        # the per-project state directory, so a tracker in a copy that came back
        # is reported like any other stray.
        self._put("_harness/tracker-build-it.md", tk.render(GOOD))
        self._put("_harness/tracker.md", tk.render(GOOD))
        count, findings = self._findings()
        self.assertEqual(count, 2)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all("outside a tracker's place" in f for f in findings))

    def test_a_malformed_tracker_is_named(self) -> None:
        self._put("tasks/build-it/tracker.md", tk.render(GOOD).replace("status: queued", "status: complete"))
        count, findings = self._findings()
        self.assertEqual(count, 1)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].startswith("demo/tasks/build-it/tracker.md: `status: complete`"))

    def test_a_tracker_names_the_place_it_sits_in(self) -> None:
        self._put("tracker.md", tk.render(GOOD))  # a project tracker naming a task
        self._put("tasks/other/tracker.md", tk.render(GOOD))  # the wrong task
        self._put("tasks/build-it/tracker.md", tk.render(dataclasses.replace(GOOD, project="elsewhere")))
        _count, findings = self._findings()
        joined = "\n".join(findings)
        self.assertIn("demo/tracker.md: a project's own tracker names no task", joined)
        self.assertIn("demo/tasks/other/tracker.md: `task: build-it` is not the task it sits beside (`other`)", joined)
        self.assertIn("demo/tasks/build-it/tracker.md: `project: elsewhere` is not the project it sits in", joined)

    def test_a_tracker_outside_its_place_is_reported(self) -> None:
        self._put("research/copied-tracker.md", tk.render(GOOD))
        count, findings = self._findings()
        self.assertEqual(count, 1)
        self.assertIn("outside a tracker's place", findings[0])

    def test_retired_and_hidden_projects_are_skipped(self) -> None:
        for rel in ("_archive/old/tracker.md", ".trash/x/tracker.md"):
            path = self.projects / rel
            path.parent.mkdir(parents=True)
            path.write_text("not a tracker\n", encoding="utf-8")
        self.assertEqual(self._findings(), (0, []))

    def _main(self, *argv: str) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gate.main(list(argv))
        return rc, out.getvalue()

    def test_the_exit_codes_follow_the_findings(self) -> None:
        self._put("tasks/build-it/tracker.md", tk.render(GOOD))
        self.assertEqual(self._main("--projects", str(self.projects))[0], 0)
        self._put("tasks/broken/tracker.md", "---\nkind: tracker\n---\n")
        rc, out = self._main("--projects", str(self.projects))
        self.assertEqual(rc, 1)
        self.assertIn("demo/tasks/broken/tracker.md", out)

    def test_a_missing_projects_space_is_nothing_to_check(self) -> None:
        rc, out = self._main("--projects", str(self.projects / "absent"))
        self.assertEqual(rc, 0)
        self.assertIn("nothing else to check", out)

    def test_the_self_test_passes(self) -> None:
        self.assertEqual(self._main("--self-test"), (0, "check-tracker-schema: self-test OK\n"))

    def test_a_tracker_saved_with_windows_line_endings_is_still_found(self) -> None:
        # A note saved on Windows ends its lines in CRLF, and the gate's head read
        # must find its frontmatter all the same.
        path = self.project / "research" / "copied-tracker.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(tk.render(GOOD).replace("\n", "\r\n").encode("utf-8"))
        count, findings = self._findings()
        self.assertEqual(count, 1)
        self.assertIn("outside a tracker's place", findings[0])

    def test_a_failed_self_test_says_so_where_the_caller_reads(self) -> None:
        from unittest import mock
        with mock.patch.object(gate, "projects_findings", return_value=(0, [])):
            rc, out = self._main("--self-test")
        self.assertEqual(rc, 1)
        self.assertIn("self-test FAILED", out)


if __name__ == "__main__":
    unittest.main()
