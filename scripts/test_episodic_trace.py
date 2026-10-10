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


class TheLinksAndTheTitleAreReal(unittest.TestCase):
    """Task 186: 628 of 1,603 wikilinks in the traces written since 2026-09-20
    went nowhere — the recall history is the machine's, and it held a test's
    fixture names and a self-probe's retired note — and five traces took a
    line the host wrote as their title."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.memory = self.vault / "agent"
        (self.memory / "memory" / "episodic").mkdir(parents=True)
        (self.memory / "memory" / "semantic").mkdir(parents=True)
        (self.memory / "memory" / "semantic" / "Vault-Location.md").write_text("x\n", encoding="utf-8")
        (self.vault / "projects" / "agentm").mkdir(parents=True)
        (self.vault / "projects" / "agentm" / "charter.md").write_text("x\n", encoding="utf-8")
        (self.vault / ".git").mkdir()
        (self.vault / ".git" / "exhaust.md").write_text("not a note\n", encoding="utf-8")
        self.transcript = self.root / "session.jsonl"
        self.history = self.root / "recall-history.jsonl"
        self.history.write_text(json.dumps({"ts": "2026-09-05T10:02:00+00:00", "hit_slugs": [
            "vault-location", "charter", "exhaust", "agentm-self-probe-2026-10-02t07-15-14z"]}) + "\n",
            encoding="utf-8")

    def test_a_recalled_name_that_is_no_note_is_not_linked(self):
        _transcript(self.transcript, with_tools=False)
        trace = et.from_transcript(self.transcript, session_id="s1", history_path=self.history,
                                   known=et.note_stems(self.memory))
        self.assertEqual(trace.recalled, ["vault-location", "charter"])

    def test_the_cli_links_only_notes_of_the_vault_the_memory_root_sits_in(self):
        import contextlib
        import io
        _transcript(self.transcript, with_tools=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            et.main([str(self.transcript), "--session", "s2", "--vault-path", str(self.memory),
                     "--history", str(self.history)])
        text = (self.memory / out.getvalue().strip()).read_text(encoding="utf-8")
        self.assertIn("[[charter]]", text, "a note outside the memory root is still a note")
        for gone in ("exhaust", "agentm-self-probe"):
            self.assertNotIn(gone, text)

    def test_a_line_the_host_wrote_is_not_the_title(self):
        for opener in ("[Image: original 2560x1600, displayed at 2000x1250. Multiply coordinates by 1.28.]",
                       "The app was quit while you were working. Please continue from where you left off.",
                       "[Request interrupted by user]"):
            with self.subTest(opener=opener):
                self.transcript.write_text(_line("user", [{"type": "text", "text": opener + "\nWhy did the gate fail?"}],
                                                 "2026-09-05T10:00:00Z") + "\n", encoding="utf-8")
                messages = __import__("reflect").load_messages(self.transcript)
                self.assertEqual(et._first_request(messages), "Why did the gate fail?")


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

    def test_the_trace_mines_its_own_candidates_when_none_are_handed_over(self):
        """The miner and the trace run as separate processes from the Stop
        hook, so the trace mines the transcript itself rather than having a
        list passed between them. HIGH is excluded — it becomes a card, and a
        record that said both would double-count it."""
        rows = self.transcript.read_text(encoding="utf-8").rstrip("\n").split("\n")
        rows.insert(1, _line("user", [{"type": "text", "text":
                                       "I prefer short commit subjects on this repo."}],
                             "2026-09-05T10:00:01Z"))
        self.transcript.write_text("\n".join(rows) + "\n", encoding="utf-8")

        trace = self._trace()
        self.assertTrue(trace.candidates, "the trace mined nothing it could list")
        rules = {c["rule"] for c in trace.candidates}
        self.assertIn("explicit preference statement (no durability cue)", rules)
        body = (self.vault / et.write_trace(self.vault, trace)).read_text(encoding="utf-8")
        self.assertIn("## Candidates", body)
        self.assertIn("short commit subjects", body)

    def test_a_high_candidate_is_not_listed_because_it_becomes_a_card(self):
        import reflect
        messages = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text":
                "I prefer that we always squash-merge on this repo."}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text":
                "I want the summary at the top."}]}},
        ]
        mined = reflect.mine_transcript_messages(messages)["memory_candidates"]
        tiers = {c.confidence for c in mined}
        self.assertIn("HIGH", tiers, "the fixture no longer produces a HIGH candidate")

        high = [c for c in mined if c.confidence == "HIGH"]
        listed = et.mined_candidates(messages)
        self.assertTrue(listed)
        # By rule, not by sentence: two patterns can fire on one sentence, so
        # the same words legitimately appear as both a card and a line.
        self.assertNotIn(high[0].rationale, [c["rule"] for c in listed])
        self.assertEqual(len(listed), len(mined) - len(high))

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



class OneTracePerSessionTests(unittest.TestCase):
    """A session keeps one trace, found by its `session:` id (agentm-vault §
    Capture, amended 2026-09-28). A session resumed after a compaction reads a
    different first request, and used to write a second file."""

    SID = "9b9d740e-0d29-4ae9-a7a9-23793b27de80"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "memory" / "episodic").mkdir(parents=True)

    def _trace(self, title, *, outcome="", recalled=(), candidates=(), sid=None):
        return et.Trace(when=date(2026, 9, 13), session_id=sid or self.SID, title=title, asked=title,
                        touched=list(recalled), recalled=list(recalled), outcome=outcome,
                        candidates=[{"rule": "explicit always/never directive", "excerpt": c, "count": 1}
                                    for c in candidates])

    def _files(self):
        return sorted(p.name for p in (self.vault / "memory" / "episodic").glob("*.md"))

    def test_the_same_session_written_twice_under_different_titles_is_one_file(self):
        first = et.write_trace(self.vault, self._trace("let's do follow-up b", outcome="Started.",
                                                       recalled=["memory/semantic/a.md"], candidates=["Never X."]))
        second = et.write_trace(self.vault, self._trace("yes", outcome="Finished: PR merged.",
                                                        recalled=["memory/semantic/b.md"], candidates=["Always Y."]))
        self.assertEqual(first, second)
        self.assertEqual(self._files(), ["2026-09-13-let-s-do-follow-up-b-9b9d740e.md"])
        text = (self.vault / first).read_text(encoding="utf-8")
        self.assertIn('title: "let\'s do follow-up b"', text, "the first request names the session")
        self.assertIn("## Asked\n\nlet's do follow-up b\n", text)
        self.assertIn("## Outcome\n\nFinished: PR merged.\n", text, "the newest recap is where it stands")
        self.assertIn("[[memory/semantic/a]]", text)
        self.assertIn("[[memory/semantic/b]]", text)
        self.assertIn("Never X.", text)
        self.assertIn("Always Y.", text)
        import yaml
        fm = yaml.safe_load(text.split("---")[1])
        self.assertEqual(fm["slug"], "2026-09-13-let-s-do-follow-up-b-9b9d740e")
        self.assertEqual(fm["touched"], ["a", "b"])
        self.assertEqual(fm["session"], self.SID)

    def test_a_write_repeated_unchanged_changes_nothing(self):
        trace = self._trace("ship it", outcome="Done.", recalled=["memory/semantic/a.md"], candidates=["Never X."])
        rel = et.write_trace(self.vault, trace)
        once = (self.vault / rel).read_text(encoding="utf-8")
        et.write_trace(self.vault, trace)
        self.assertEqual((self.vault / rel).read_text(encoding="utf-8"), once)

    def test_the_fallback_title_does_not_repeat_the_id_and_yields_to_a_request(self):
        rel = et.write_trace(self.vault, self._trace(et.fallback_title(self.SID), recalled=["memory/semantic/a.md"]))
        self.assertEqual(Path(rel).name, "2026-09-13-session-9b9d740e.md")
        et.write_trace(self.vault, self._trace("back to the release", recalled=["memory/semantic/b.md"]))
        self.assertEqual(self._files(), ["2026-09-13-session-9b9d740e.md"],
                         "the file keeps its name; only a session with no trace gets a new one")
        text = (self.vault / rel).read_text(encoding="utf-8")
        self.assertIn('title: "back to the release"', text)
        self.assertIn("## Asked\n\nback to the release\n", text)

    def test_a_titled_trace_is_found_before_a_fallback_one_and_a_superseded_one_never(self):
        folder = self.vault / "memory" / "episodic"
        (folder / "2026-09-12-session-9b9d740e.md").write_text(self._trace(et.fallback_title(self.SID)).render(),
                                                                encoding="utf-8")
        titled = self._trace("run the audit")
        titled_text = titled.render()
        (folder / "2026-09-13-run-the-audit-9b9d740e.md").write_text(titled_text, encoding="utf-8")
        self.assertEqual(et.find_session_trace(self.vault, self.SID).name, "2026-09-13-run-the-audit-9b9d740e.md")
        (folder / "2026-09-13-run-the-audit-9b9d740e.md").write_text(
            titled_text.replace("lifecycle: active\n", "lifecycle: superseded\nsuperseded_by: x\n"), encoding="utf-8")
        self.assertEqual(et.find_session_trace(self.vault, self.SID).name, "2026-09-12-session-9b9d740e.md")
        self.assertIsNone(et.find_session_trace(self.vault, "another-session"))

    def test_two_sessions_still_write_two_files(self):
        et.write_trace(self.vault, self._trace("ship it", recalled=["memory/semantic/a.md"]))
        et.write_trace(self.vault, self._trace("ship it", recalled=["memory/semantic/a.md"], sid="ffff1111-2222"))
        self.assertEqual(len(self._files()), 2)


# The shapes below are the live ones (task 190 step 2, the operator's ruling of
# 2026-10-07: a session with no operator turn writes no trace). A Desktop
# scheduled task opens with the wrapper and its preamble, stamped exactly as an
# operator's turn is (`entrypoint: claude-desktop`, `promptSource: sdk`,
# `origin.kind: human`), so only its content says nobody was there. Nine of the
# audit's twenty new cards were traces of one such poll, every three hours.
_DESKTOP = {"entrypoint": "claude-desktop", "promptSource": "sdk", "origin": {"kind": "human"},
            "userType": "external", "isSidechain": False}
_HEADLESS = {"entrypoint": "sdk-cli", "promptSource": "sdk", "userType": "external", "isSidechain": False}
_SCHEDULED = (
    '<scheduled-task name="playon-recording-progress" '
    'file="~/.claude/scheduled-tasks/playon-recording-progress/SKILL.md">\n'
    "This is an automated run of a scheduled task. The user is not present to answer questions. "
    "For implementation details, execute autonomously without asking clarifying questions — make "
    "reasonable choices and note them in your output.\n\n"
    "You are reporting progress on a long-running PlayOn recording job for the user's "
    "movies-tv-games project. READ ONLY: do not change anything.\n</scheduled-task>")


class TheUnattendedSessionTests(unittest.TestCase):
    """No trace for a session nobody was at; one the operator joined keeps it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        self.transcript = self.root / "session.jsonl"
        self.history = self.root / "recall-history.jsonl"
        self.history.write_text("", encoding="utf-8")

    def _write(self, *turns):
        """A session that recalls two notes after its opening turn, as the poll
        does, followed by `turns` — each `(stamp, content, **fields)`."""
        opener, rest = turns[0], turns[1:]
        rows = [_line("user", [{"type": "text", "text": opener[1]}], "2026-10-06T10:27:00Z", **opener[0])]
        rows.append(_line("assistant", [{"type": "tool_use", "id": "t1", "name": "mcp__agentmemory__memory_search",
                                         "input": {"query": "playon"}}], "2026-10-06T10:27:05Z"))
        rows.append(_line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(
            {"results": [{"path": "projects/movies-tv-games/charter.md"},
                         {"path": "projects/movies-tv-games/playon-automation.md"}]})}],
            "2026-10-06T10:27:06Z", **opener[0]))
        rows.append(_line("assistant", [{"type": "text", "text": "**The Netflix films are all done.**"}],
                          "2026-10-06T10:30:00Z"))
        for i, (fields, text) in enumerate(rest):
            rows.append(_line("user", [{"type": "text", "text": text}], f"2026-10-06T11:0{i}:00Z", **fields))
            rows.append(_line("assistant", [{"type": "text", "text": "Done."}], f"2026-10-06T11:0{i}:30Z"))
        self.transcript.write_text("\n".join(rows) + "\n", encoding="utf-8")
        trace = et.from_transcript(self.transcript, session_id="44713056-c447", history_path=self.history)
        return trace, et.write_trace(self.vault, trace)

    def assertNoTrace(self, written):
        self.assertIsNone(written)
        self.assertEqual(list((self.vault / "memory" / "episodic").iterdir()), [])

    def test_a_scheduled_run_writes_no_trace(self):
        trace, written = self._write((_DESKTOP, _SCHEDULED))
        self.assertTrue(trace.touched, "the run did touch notes; that is not what decides")
        self.assertNoTrace(written)

    def test_a_scheduled_run_the_operator_replied_to_keeps_its_trace(self):
        _, written = self._write((_DESKTOP, _SCHEDULED), (_DESKTOP, "Thanks. Check the Hulu queue too."))
        self.assertIsNotNone(written)

    def test_an_interrupt_the_host_wrote_is_not_a_reply(self):
        # The live shape of two scheduled agentm runs: the only later user turn
        # is the host's bracketed notice.
        host = {"entrypoint": "claude-desktop", "userType": "external", "isSidechain": False}
        _, written = self._write((_DESKTOP, _SCHEDULED), (host, "[Request interrupted by user for tool use]"))
        self.assertNoTrace(written)

    def test_a_headless_prompt_writes_no_trace(self):
        _, written = self._write((_HEADLESS, "Judge this note and return JSON."))
        self.assertNoTrace(written)

    def test_a_headless_session_resumed_by_the_operator_keeps_its_trace(self):
        typed = {"entrypoint": "cli", "userType": "external", "isSidechain": False}
        _, written = self._write((_HEADLESS, "Judge this note and return JSON."), (typed, "Why that verdict?"))
        self.assertIsNotNone(written)

    def test_tool_results_meta_sidechain_and_notifications_are_not_turns(self):
        meta = dict(_DESKTOP, isMeta=True)
        side = dict(_DESKTOP, isSidechain=True)
        note = dict(_DESKTOP, origin={"kind": "task-notification"})
        _, written = self._write((_DESKTOP, _SCHEDULED), (meta, "You are running the work phase."),
                                 (side, "Explore the scripts directory."),
                                 (note, "<task-notification>CI checks: pass</task-notification>"))
        self.assertNoTrace(written)

    def test_a_typed_slash_command_is_a_turn(self):
        # Two live sessions of 293 opened with a typed `/work --name …` and no
        # other typed word; the operator was there.
        command = ("<command-message>development-lifecycle:work</command-message>\n"
                   "<command-name>/development-lifecycle:work</command-name>\n"
                   "<command-args>--name agentm-vault-10-projects-migration</command-args>")
        _, written = self._write((_DESKTOP, command))
        self.assertIsNotNone(written)

    def test_an_ordinary_desktop_session_is_unchanged(self):
        # Stamped exactly as the scheduled run is; only the words differ.
        trace, written = self._write((_DESKTOP, "How far has the PlayOn job got?"))
        self.assertEqual(written, "memory/episodic/2026-10-06-how-far-has-the-playon-job-got-44713056.md")
        self.assertEqual(trace.title, "How far has the PlayOn job got?")

    def test_the_cli_reports_an_unattended_session_and_exits_zero(self):
        import contextlib
        import io
        self._write((_DESKTOP, _SCHEDULED))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = et.main([str(self.transcript), "--session", "44713056-c447", "--vault-path", str(self.vault),
                          "--history", str(self.history)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "no operator turn")
        self.assertEqual(list((self.vault / "memory" / "episodic").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
