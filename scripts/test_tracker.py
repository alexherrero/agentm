#!/usr/bin/env python3
"""Tests for `scripts/tracker.py` — the one tracker schema (agentm-vault plan 09, task 2).

A tracker round-trips through render and parse; every transition in the table
is exercised and every other one refused; the schema's findings each fire; the
file writes are atomic and refuse a file that changed under them; the command
line crickets shells to behaves the same way.

Run directly:

    python3 scripts/test_tracker.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import tracker as tk  # noqa: E402

TODAY = "2026-09-12"


def _tracker(**overrides) -> tk.Tracker:
    fields = dict(
        title="Measure online recall", project="agentm", task="measure-online-recall",
        status="active", importance=7, opened="2026-08-29", updated="2026-09-05",
        closed=None, issue=512, design="wiki/designs/agentm-hybrid-retrieval.md",
        objective="Recall answers from the vault, measured online.",
        state="The harness runs nightly.\nThe gate reads R@5 0.688.",
        next_steps="1. Read the morning note.\n2. Re-run the gate.",
        outcome="",
    )
    fields.update(overrides)
    return tk.Tracker(**fields)


def _at(status: str) -> tk.Tracker:
    """A valid tracker standing at `status`."""
    if status in tk.FINAL:
        return _tracker(status=status, closed="2026-09-06", outcome="It shipped.")
    return _tracker(status=status)


class RoundTrip(unittest.TestCase):
    def assertRoundTrips(self, t: tk.Tracker) -> None:
        text = tk.render(t)
        self.assertEqual(tk.parse(text), t)
        self.assertEqual(tk.render(tk.parse(text)), text)
        self.assertEqual(tk.findings(t), [])

    def test_a_task_tracker_with_every_field(self) -> None:
        self.assertRoundTrips(_tracker())

    def test_a_project_tracker_carries_no_task(self) -> None:
        t = _tracker(task=None, issue=None, design=None, importance=None)
        self.assertRoundTrips(t)
        self.assertNotIn("task:", tk.render(t))

    def test_a_closed_tracker_with_its_outcome(self) -> None:
        self.assertRoundTrips(_at("done"))

    def test_a_title_that_needs_quoting(self) -> None:
        for title in ('Fix: the "#1" thing', "yes", "2026", "trailing ", "a # b"):
            with self.subTest(title=title):
                self.assertRoundTrips(_tracker(title=title))

    def test_the_rendered_shape(self) -> None:
        text = tk.render(_tracker())
        head, body = text.split("\n---\n", 1)
        self.assertEqual(
            [line.split(":", 1)[0] for line in head.split("\n")[1:]],
            ["kind", "title", "project", "task", "status", "importance",
             "opened", "updated", "closed", "issue", "design"],
        )
        self.assertIn("closed:\n", text)
        self.assertEqual([line for line in body.split("\n") if line.startswith("## ")],
                         ["## Objective", "## State", "## Next", "## Outcome"])

    def test_the_frontmatter_parses_as_yaml(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        head = tk.render(_tracker(title='Fix: the "#1" thing')).split("\n---\n", 1)[0][4:]
        data = yaml.safe_load(head)
        self.assertEqual(data["title"], 'Fix: the "#1" thing')
        self.assertEqual(data["kind"], "tracker")
        self.assertIsNone(data["closed"])
        self.assertEqual(data["issue"], 512)


class Transitions(unittest.TestCase):
    def test_every_legal_transition(self) -> None:
        for frm, targets in tk.TRANSITIONS.items():
            for to in targets:
                with self.subTest(frm=frm, to=to):
                    outcome = "Withdrawn: superseded." if to in tk.FINAL else None
                    after = tk.transition(_at(frm), to, today=TODAY, outcome=outcome)
                    self.assertEqual((after.status, after.updated), (to, TODAY))
                    self.assertEqual(after.closed, TODAY if to in tk.FINAL else None)
                    self.assertEqual(tk.findings(after), [])

    def test_every_other_transition_is_refused(self) -> None:
        for frm in tk.STATUSES:
            for to in tk.STATUSES:
                if to in tk.TRANSITIONS[frm]:
                    continue
                with self.subTest(frm=frm, to=to):
                    with self.assertRaises(tk.TrackerError):
                        tk.transition(_at(frm), to, today=TODAY, outcome="x")

    def test_the_final_statuses_go_nowhere(self) -> None:
        for status in tk.FINAL:
            self.assertEqual(tk.TRANSITIONS[status], ())

    def test_closing_writes_the_outcome(self) -> None:
        with self.assertRaisesRegex(tk.TrackerError, "Outcome"):
            tk.transition(_at("active"), "done", today=TODAY)
        after = tk.transition(_at("active"), "done", today=TODAY, outcome="It shipped in #618.")
        self.assertEqual((after.outcome, after.closed), ("It shipped in #618.", TODAY))

    def test_a_transition_rewrites_state_and_next(self) -> None:
        after = tk.transition(_at("active"), "parked", today=TODAY,
                              state="Parked: waiting on plan 07.", next_steps="Resume after 07.")
        self.assertEqual((after.state, after.next_steps), ("Parked: waiting on plan 07.", "Resume after 07."))

    def test_an_unknown_status_or_date_is_refused(self) -> None:
        with self.assertRaises(tk.TrackerError):
            tk.transition(_at("active"), "complete", today=TODAY)
        with self.assertRaises(tk.TrackerError):
            tk.transition(_at("active"), "parked", today="12/09/2026")


class Findings(unittest.TestCase):
    def assertFinds(self, fragment: str, t: tk.Tracker) -> None:
        found = tk.findings(t)
        self.assertTrue(any(fragment in f for f in found), f"{fragment!r} not in {found}")

    def test_each_rule(self) -> None:
        self.assertFinds("`status: complete`", _tracker(status="complete"))
        self.assertFinds("has no `closed` date", _tracker(status="done", outcome="x"))
        self.assertFinds("`closed` is set on a `active`", _tracker(closed="2026-09-06"))
        self.assertFinds("`opened` is not a date", _tracker(opened="yesterday"))
        self.assertFinds("`updated` is before `opened`", _tracker(updated="2026-08-01"))
        self.assertFinds("outside 1-10", _tracker(importance=11))
        self.assertFinds("not an issue number", _tracker(issue=0))
        self.assertFinds("single path component", _tracker(task="../escape"))
        self.assertFinds("`project` is empty", _tracker(project=""))
        self.assertFinds("has no Outcome", _tracker(status="dropped", closed="2026-09-06"))
        self.assertFinds("the Objective is empty", _tracker(objective=""))


class ParseErrors(unittest.TestCase):
    def assertRefused(self, fragment: str, text: str) -> None:
        with self.assertRaisesRegex(tk.TrackerError, fragment):
            tk.parse(text)

    def test_each_structural_error(self) -> None:
        good = tk.render(_tracker())
        self.assertRefused("no frontmatter", "# just a note\n")
        self.assertRefused("not closed", "---\nkind: tracker\n")
        self.assertRefused("not a tracker field", good.replace("status: active", "status: active\nowner: me"))
        self.assertRefused("appears twice", good.replace("status: active", "status: active\nstatus: done"))
        self.assertRefused("is not `tracker`", good.replace("kind: tracker", "kind: plan"))
        self.assertRefused("missing `closed`", good.replace("closed:\n", ""))
        self.assertRefused("not .* in that order", good.replace("## State", "## Status"))
        swapped = good.replace("## Next", "## @").replace("## Outcome", "## Next").replace("## @", "## Outcome")
        self.assertRefused("not .* in that order", swapped)
        self.assertRefused("text before", good.replace("---\n\n## Objective", "---\n\nhello\n\n## Objective"))
        self.assertRefused("not a whole number", good.replace("importance: 7", "importance: high"))

    def test_check_text_reports_instead_of_raising(self) -> None:
        self.assertEqual(tk.check_text(tk.render(_tracker())), [])
        self.assertEqual(len(tk.check_text("# not a tracker\n")), 1)


class New(unittest.TestCase):
    def test_a_new_tracker_is_queued(self) -> None:
        t = tk.new(title="Build the brief", project="agentm", task="build-the-brief",
                   objective="A session opens on twenty lines.", next_step="Read the hook.", today=TODAY)
        self.assertEqual((t.status, t.state, t.next_steps), ("queued", tk.NOT_STARTED, "Read the hook."))
        self.assertEqual((t.opened, t.updated, t.closed), (TODAY, TODAY, None))

    def test_a_bad_new_tracker_is_refused(self) -> None:
        with self.assertRaises(tk.TrackerError):
            tk.new(title="x", project="a/b", objective="y", next_step="z", today=TODAY)


class Files(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agentm-tracker-"))
        self.path = self.tmp / "tasks" / "foo" / "tracker.md"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_write_reads_back(self) -> None:
        tk.write(self.path, _tracker())
        t, digest = tk.read(self.path)
        self.assertEqual(t, _tracker())
        self.assertEqual(digest, tk.content_hash(self.path.read_text(encoding="utf-8")))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["tracker.md"])

    def test_a_changed_file_is_refused(self) -> None:
        tk.write(self.path, _tracker())
        _t, digest = tk.read(self.path)
        tk.write(self.path, _tracker(state="Someone else wrote this."))
        with self.assertRaises(tk.ChangedError):
            tk.write(self.path, _tracker(state="Mine."), expected_hash=digest)
        self.assertIn("Someone else wrote this.", self.path.read_text(encoding="utf-8"))

    def test_a_tracker_with_findings_is_not_written(self) -> None:
        with self.assertRaises(tk.TrackerError):
            tk.write(self.path, _tracker(status="complete"))
        self.assertFalse(self.path.exists())


class CommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agentm-tracker-cli-"))
        self.path = self.tmp / "tracker.md"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tk.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def _new(self) -> int:
        rc, _out, _err = self._main("new", "--title", "Build the brief", "--project", "agentm",
                                    "--task", "build-the-brief", "--objective", "Twenty lines.",
                                    "--next", "Read the hook.", "--today", TODAY, "--out", str(self.path))
        return rc

    def test_new_show_transition_check(self) -> None:
        self.assertEqual(self._new(), 0)
        self.assertEqual(self._new(), 1)  # a tracker is opened once
        rc, out, _ = self._main("show", str(self.path))
        self.assertEqual(rc, 0)
        shown = json.loads(out)
        self.assertEqual((shown["status"], shown["sections"]["Next"]), ("queued", "Read the hook."))
        rc, out, _ = self._main("transition", str(self.path), "--to", "active",
                                "--state", "Started.", "--today", "2026-09-13")
        self.assertEqual(rc, 0)
        self.assertEqual(tk.read(self.path)[0].status, "active")
        self.assertEqual(self._main("check", str(self.path))[0], 0)

    def test_an_illegal_transition_leaves_the_file_alone(self) -> None:
        self._new()
        before = self.path.read_bytes()
        rc, _out, err = self._main("transition", str(self.path), "--to", "done", "--outcome", "x")
        self.assertEqual(rc, 1)
        self.assertIn("cannot become `done`", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_check_reports_a_malformed_tracker(self) -> None:
        self.path.write_text("---\nkind: tracker\ntitle: x\n---\n", encoding="utf-8")
        rc, out, _ = self._main("check", str(self.path))
        self.assertEqual(rc, 1)
        self.assertIn("missing", out)


if __name__ == "__main__":
    unittest.main()
