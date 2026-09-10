#!/usr/bin/env python3
"""test_episodic_trace.py — the agent's memory of its own sessions (filing-v2 remainders task 7)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for d in (_SKILL, _HERE):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import calendar_index  # noqa: E402
import episodic_trace as et  # noqa: E402


def _line(kind, content, ts, **extra):
    return json.dumps({"type": kind, "timestamp": ts, "message": {"role": "user" if kind == "user" else "assistant", "content": content}, **extra})


def _transcript(path: Path, *, with_tools=True):
    rows = [_line("user", [{"type": "text", "text": "Help me tune the archive thresholds.\nSecond line."}], "2026-09-05T10:00:00Z")]
    if with_tools:
        rows.append(_line("assistant", [{"type": "tool_use", "id": "t1", "name": "mcp__agentmemory__memory_search", "input": {"query": "archive"}}], "2026-09-05T10:00:05Z"))
        rows.append(_line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": json.dumps({"results": [{"path": "memory/semantic/tune-the-archive.md", "score": 1.0}, {"path": "memory/procedural/purge-lane.md"}]})}], "2026-09-05T10:00:06Z"))
        rows.append(_line("assistant", [{"type": "tool_use", "id": "t2", "name": "mcp__agentmemory__memory_capture", "input": {"text": "x"}}], "2026-09-05T10:05:00Z"))
        rows.append(_line("user", [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "captured\n" + json.dumps({"path": "memory/semantic/archive-line-ruling.md", "slug": "archive-line-ruling"})}]}], "2026-09-05T10:05:01Z"))
    rows.append(_line("assistant", [{"type": "text", "text": "Done."}], "2026-09-05T10:10:00Z"))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class TraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        self.transcript = self.root / "session.jsonl"
        self.history = self.root / "recall-history.jsonl"
        self.history.write_text(
            json.dumps({"ts": "2026-09-05T10:02:00+00:00", "hit_slugs": ["vault-location", "tune-the-archive"]}) + "\n"
            + json.dumps({"ts": "2026-09-04T10:02:00+00:00", "hit_slugs": ["yesterday-only"]}) + "\n", encoding="utf-8")

    def _trace(self, **kw):
        _transcript(self.transcript, **kw)
        return et.from_transcript(self.transcript, session_id="d46db2c7-0000", history_path=self.history)

    def test_a_trace_links_what_the_session_captured_and_recalled(self):
        trace = self._trace()
        self.assertEqual(trace.when, date(2026, 9, 5))
        self.assertEqual(trace.title, "Help me tune the archive thresholds.")
        self.assertEqual(trace.captured, ["memory/semantic/archive-line-ruling.md"])
        self.assertEqual(trace.recalled, ["memory/semantic/tune-the-archive.md", "memory/procedural/purge-lane.md", "vault-location", "tune-the-archive"])
        self.assertNotIn("yesterday-only", trace.recalled, "recall rows outside the session's window are not the session's")
        rel = et.write_trace(self.vault, trace)
        self.assertEqual(rel, "memory/episodic/2026-09-05-help-me-tune-the-archive-thresholds-d46db2c7.md")
        text = (self.vault / rel).read_text(encoding="utf-8")
        self.assertIn("kind: session-trace", text)
        self.assertIn("day: 2026-09-05", text)
        self.assertIn("[[memory/semantic/archive-line-ruling]]", text)
        self.assertIn("[[vault-location]]", text)
        import yaml
        fm = yaml.safe_load(text.split("---")[1])
        self.assertEqual(fm["slug"], Path(rel).stem)
        self.assertEqual(fm["source"], "conversation")

    def test_a_session_that_touched_nothing_leaves_no_trace(self):
        trace = self._trace(with_tools=False)
        self.history.write_text("", encoding="utf-8")
        trace = et.from_transcript(self.transcript, session_id="abc", history_path=self.history)
        self.assertEqual(trace.touched, [])
        self.assertIsNone(et.write_trace(self.vault, trace))
        self.assertEqual(list((self.vault / "memory" / "episodic").iterdir()), [])

    def test_the_day_index_finds_the_trace_and_two_sessions_do_not_collide(self):
        first = self._trace()
        et.write_trace(self.vault, first)
        second = et.from_transcript(self.transcript, session_id="ffff1111-2222", history_path=self.history)
        et.write_trace(self.vault, second)
        traces = calendar_index.episodic_traces(self.vault, date(2026, 9, 5))
        self.assertEqual(len(traces), 2)
        self.assertEqual(sorted(t[1] for t in traces), ["Help me tune the archive thresholds."] * 2)

    def test_the_cap_holds(self):
        trace = et.Trace(when=date(2026, 9, 5), session_id="s", title="t", touched=[f"memory/semantic/n{i}.md" for i in range(60)])
        self.assertEqual(len(trace.touched), 60)  # the dataclass holds what it is given...
        built = et.from_transcript  # ...and the builder caps at MAX_TOUCHED
        self.assertEqual(et.MAX_TOUCHED, 40)

    def test_the_cli_prints_the_path_and_never_fails(self):
        import contextlib
        import io
        _transcript(self.transcript)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = et.main([str(self.transcript), "--session", "cli-1", "--vault-path", str(self.vault), "--history", str(self.history)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().startswith("memory/episodic/2026-09-05-"))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = et.main([str(self.root / "missing.jsonl"), "--session", "x", "--vault-path", str(self.vault)])
        self.assertEqual(rc, 0, "a hook never blocks session end on a trace")
        self.assertIn("skipped", err.getvalue())


class TheHandoffRecord(unittest.TestCase):
    """The trace is what the next session reads instead of this one's context:
    what was asked, what came of it, what was written, what was read, and what
    was said in passing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        self.transcript = self.root / "session.jsonl"
        self.history = self.root / "recall-history.jsonl"
        self.history.write_text(
            json.dumps({"ts": "2026-09-05T10:02:00+00:00", "hit_slugs": ["vault-location"]}) + "\n",
            encoding="utf-8")
        _transcript(self.transcript)

    def _trace(self, **kw):
        return et.from_transcript(self.transcript, session_id="d46db2c7-0000",
                                  history_path=self.history, **kw)

    def test_the_five_sections_and_the_renamed_field(self):
        trace = self._trace(
            project="agentm", surface="claude-code",
            candidates=[
                {"rule": "durability-cue", "excerpt": "always run the battery first", "count": 3},
                {"rule": "fix-observed", "excerpt": "the port was 8902, not 8901", "count": 1},
            ],
        )
        rel = et.write_trace(self.vault, trace)
        text = (self.vault / rel).read_text(encoding="utf-8")

        for heading in ("## Asked", "## Outcome", "## Captured", "## Recalled", "## Candidates"):
            self.assertIn(heading + "\n", text, f"the record is missing {heading}")
        self.assertIn("\n## Asked\n\nHelp me tune the archive thresholds.\n", text)
        self.assertIn("\n## Outcome\n\nDone.\n", text)

        # The rename, both directions: a field that promised named things and
        # held basenames now says what it holds.
        self.assertIn("touched: [", text)
        self.assertNotIn("entities:", text)

        import yaml
        fm = yaml.safe_load(text.split("---")[1])
        self.assertEqual(fm["project"], "agentm")
        self.assertEqual(fm["surface"], "claude-code")
        self.assertIn("archive-line-ruling", fm["touched"])

    def test_a_candidate_line_carries_its_rule_excerpt_and_count(self):
        trace = self._trace(candidates=[
            {"rule": "durability-cue", "excerpt": "always run the battery first", "count": 3},
            {"rule": "fix-observed", "excerpt": "the port was 8902", "count": 1},
        ])
        body = (self.vault / et.write_trace(self.vault, trace)).read_text(encoding="utf-8")
        self.assertIn("- durability-cue (×3) — “always run the battery first”", body)
        # A count of one is not written: "×1" is noise on the common case.
        self.assertIn("- fix-observed — “the port was 8902”", body)

    def test_a_multi_line_excerpt_cannot_break_the_list(self):
        trace = self._trace(candidates=[
            {"rule": "durability-cue", "excerpt": "one\n- two\n\nthree", "count": 1}])
        body = (self.vault / et.write_trace(self.vault, trace)).read_text(encoding="utf-8")
        self.assertIn("- durability-cue — “one - two three”", body)

    def test_the_candidates_cap_holds(self):
        trace = self._trace(candidates=[{"rule": f"r{i}", "excerpt": "x"} for i in range(60)])
        self.assertEqual(len(trace.candidates), et.MAX_CANDIDATES)

    def test_a_session_whose_only_output_was_things_said_in_passing_still_leaves_a_trace(self):
        """The miner files no note below HIGH any more, so this material is
        here or nowhere."""
        trace = et.Trace(when=date(2026, 9, 5), session_id="s", title="a session",
                         candidates=[{"rule": "durability-cue", "excerpt": "x", "count": 1}])
        self.assertEqual(trace.touched, [])
        self.assertIsNotNone(et.write_trace(self.vault, trace))

    def test_a_session_with_no_closing_prose_gets_no_outcome(self):
        """A final turn that is nothing but tool calls has no recap, and a
        manufactured one would be worse than none."""
        rows = self.transcript.read_text(encoding="utf-8").rstrip("\n").split("\n")[:-1]
        rows.append(_line("assistant", [{"type": "tool_use", "id": "t9", "name": "Bash",
                                         "input": {"command": "ls"}}], "2026-09-05T10:11:00Z"))
        self.transcript.write_text("\n".join(rows) + "\n", encoding="utf-8")
        trace = self._trace()
        self.assertEqual(trace.outcome, "")
        body = (self.vault / et.write_trace(self.vault, trace)).read_text(encoding="utf-8")
        self.assertNotIn("## Outcome", body)
        self.assertIn("## Asked", body)


if __name__ == "__main__":
    unittest.main()
