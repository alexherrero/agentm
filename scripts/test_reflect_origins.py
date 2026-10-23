#!/usr/bin/env python3
"""The miner mines what the operator said, once, in a session they were at
(task 192 step 2, backlog #676).

Three rules, each from a shape the audits found:

- **An unattended session mines nothing.** The trace writer has refused a
  session with no operator turn since task 190; the miner did not, so a
  scheduled poll still filed cards. Both now read `reflect.has_operator_turn`.
- **A question is not a directive** (follow-ups F40). "should we always rebase
  here?" carries a directive word and was mined as one.
- **A quote is not a sighting.** A candidate whose words the session already
  carried — its own first request, `~/.claude/CLAUDE.md`, the repo's AGENTS.md,
  the always-load tier — is dropped and listed in the trace with the source it
  quotes. The one exception is an idea remark in the first request: a session
  can open on an idea, so it is kept for the night's judge, marked (the
  operator's ruling of 2026-10-16).

Run: python3 scripts/test_reflect_origins.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import episodic_trace  # noqa: E402
import reflect  # noqa: E402
import vault_layout  # noqa: E402

# The live shape of a Desktop scheduled task (task 190's audit): stamped as an
# operator's turn is, so only its words say nobody was there. It carries a
# directive the old miner filed as a HIGH preference.
_DESKTOP = {"entrypoint": "claude-desktop", "promptSource": "sdk", "origin": {"kind": "human"},
            "userType": "external", "isSidechain": False}
SCHEDULED = (
    '<scheduled-task name="playon-recording-progress" '
    'file="~/.claude/scheduled-tasks/playon-recording-progress/SKILL.md">\n'
    "This is an automated run of a scheduled task. The user is not present to answer questions. "
    "From now on I want the queue checked every three hours, and never touch the recordings.\n"
    "</scheduled-task>")

# A pasted /work brief, the 10-01 shape: two idea cards came from one. The
# brief's standing rules carry the directive words the HIGH lane files on.
BRIEF = (
    "In agentm (primary clone ~/Antigravity/agentm), run task 177's triage. "
    "This session decides the verdicts. From now on always archive a follow-up rather than delete it, "
    "and never edit the vault notes of another project. We should also file each deferral as a "
    "backlog item later, once the board is current.")

# What CLAUDE.md says, the 10-07 shape: a paragraph of it pasted into a chip brief.
CLAUDE_MD = (
    "# Global Claude Code Instructions\n\n"
    "## Commit messages\n\n"
    "Do not append a Co-Authored-By trailer to git commit messages. The user is the sole author of "
    "intent. Always refresh a stale branch by rebase, never by merging main back into it.\n")

PREFERENCE = "From now on I want the gate battery run before every commit."


def _home(path) -> dict:
    """The environment that makes `path` the home directory: `Path.home()`
    reads HOME on POSIX and USERPROFILE on Windows, so a fake home moves both
    (the Windows runner found HOME alone leaving the real one in place)."""
    return {"HOME": str(path), "USERPROFILE": str(path)}


def _turn(text, **fields):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}, **fields}


def _said(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _prefs(mined):
    return [c for c in mined["memory_candidates"] if c.category == "preferences"]


def _rules(mined):
    return [d["rule"] for d in mined["dropped"]]


class AnUnattendedSessionMinesNothing(unittest.TestCase):
    def test_a_scheduled_task_transcript_mines_nothing(self):
        mined = reflect.mine_transcript_messages([_turn(SCHEDULED, **_DESKTOP), _said("Checked the queue.")])
        self.assertFalse(mined["attended"])
        self.assertEqual(mined["memory_candidates"], [])
        self.assertEqual(mined["idea_candidates"], [])
        self.assertEqual(mined["dropped"], [])

    def test_the_same_transcript_with_an_operator_reply_mines(self):
        mined = reflect.mine_transcript_messages([
            _turn(SCHEDULED, **_DESKTOP), _said("Checked the queue."),
            _turn("Thanks. " + PREFERENCE, **_DESKTOP)])
        self.assertTrue(mined["attended"])
        self.assertIn("gate battery", " ".join(c.body for c in _prefs(mined)))

    def test_the_miner_and_the_trace_writer_read_one_answer(self):
        shapes = [
            [_turn(SCHEDULED, **_DESKTOP)],
            [_turn(SCHEDULED, **_DESKTOP), _turn("[Request interrupted by user for tool use]")],
            [_turn("Judge this note and return JSON.", entrypoint="sdk-cli", promptSource="sdk")],
            [_turn("How far has the PlayOn job got?", **_DESKTOP)],
            [_turn("<command-name>/work</command-name>\n<command-args>--name x</command-args>", **_DESKTOP)],
        ]
        for msgs in shapes:
            self.assertEqual(episodic_trace.has_operator_turn(msgs), reflect.has_operator_turn(msgs))
        self.assertEqual([reflect.has_operator_turn(m) for m in shapes], [False, False, False, True, True])

    def test_the_route_pass_files_no_card_for_a_scheduled_run(self):
        root = Path(tempfile.mkdtemp(prefix="reflect-unattended-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "memory").mkdir()
        t = root / "t.jsonl"
        t.write_text(json.dumps(_turn(SCHEDULED, **_DESKTOP)) + "\n", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, _home(root)), contextlib.redirect_stdout(out):
            reflect.main([str(t), "--vault-path", str(root), "--route", "--summary"])
        route = [json.loads(l) for l in out.getvalue().splitlines() if '"pass": "route"' in l][-1]
        self.assertFalse(route["attended"])
        self.assertEqual(route["auto_saved"], 0)
        self.assertEqual(list((root / "memory").rglob("*.md")), [])


class AQuestionIsNotADirective(unittest.TestCase):
    def _mine(self, *texts):
        return reflect.mine_transcript_messages([_turn("Look at the release branch."), *map(_turn, texts)])

    def test_should_we_always_rebase_yields_no_preference(self):
        mined = self._mine("should we always rebase here?")
        self.assertEqual(_prefs(mined), [])
        self.assertIn(reflect.QUESTION_RULE, _rules(mined))

    def test_a_question_without_its_mark_is_still_a_question(self):
        for text in ("Should we always rebase here", "can you always run the gates first",
                     "how should we never lose a trace like that"):
            self.assertEqual(_prefs(self._mine(text)), [], text)

    def test_a_question_beside_a_statement_drops_only_the_question(self):
        mined = self._mine("Do we always need the battery? " + PREFERENCE)
        titles = [c.title for c in _prefs(mined)]
        self.assertTrue(any("gate battery" in t for t in titles), titles)
        self.assertFalse(any(t.lower().startswith("always need") for t in titles), titles)
        self.assertIn(reflect.QUESTION_RULE, _rules(mined))

    def test_statements_that_open_like_questions_still_mine(self):
        for text in ("Do not use squash merges for the release branch, always rebase it.",
                     "What I want is the battery run before every commit, every time."):
            self.assertTrue(_prefs(self._mine(text)), text)

    def test_a_question_carries_no_correction_either(self):
        mined = self._mine("did I tell you to skip the gates?")
        self.assertEqual([c for c in mined["memory_candidates"] if c.title.startswith("Correction")], [])


class AQuoteIsNotASighting(unittest.TestCase):
    def test_a_candidate_quoting_a_claude_md_line_is_dropped(self):
        pasted = ("Remember the house rules. Always refresh a stale branch by rebase, never by merging main "
                  "back into it.")
        mined = reflect.mine_transcript_messages(
            [_turn("Fix the flaky gate in the reflect tests."), _turn(pasted)],
            standing={"~/.claude/CLAUDE.md": CLAUDE_MD})
        self.assertEqual(_prefs(mined), [])
        self.assertIn("not filed: quotes ~/.claude/CLAUDE.md", _rules(mined))

    def test_the_operator_restating_a_rule_in_their_own_words_still_mines(self):
        mined = reflect.mine_transcript_messages(
            [_turn("Fix the flaky gate."), _turn("I think we should always rebase before merging into main.")],
            standing={"~/.claude/CLAUDE.md": CLAUDE_MD})
        self.assertTrue(_prefs(mined))

    def test_a_candidate_from_a_pasted_brief_is_dropped(self):
        mined = reflect.mine_transcript_messages([_turn(BRIEF), _said("Starting the triage."),
                                                  _turn("Done? " + BRIEF)])
        self.assertEqual(_prefs(mined), [], "the brief's standing rules are not the operator stating them")
        self.assertIn("not filed: quotes the session's first request", _rules(mined))

    def test_an_ordinary_stated_preference_still_mines(self):
        mined = reflect.mine_transcript_messages([_turn(BRIEF), _said("Starting the triage."), _turn(PREFERENCE)])
        prefs = _prefs(mined)
        self.assertEqual([c.confidence for c in prefs], ["HIGH"])
        self.assertIn("gate battery run before every commit", prefs[0].body)

    def test_a_candidate_quoting_the_always_load_tier_is_dropped(self):
        root = Path(tempfile.mkdtemp(prefix="reflect-standards-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "agent").mkdir()
        (root / "standards").mkdir()
        (root / ".obsidian").mkdir()
        (root / "standards" / "user-preferences.md").write_text(
            "# How I want things done\n\nNever pass the delete-branch flag to gh pr merge from a worktree.\n",
            encoding="utf-8")
        msgs = [_turn("Merge the plan PR."),
                _turn("Never pass the delete-branch flag to gh pr merge from a worktree.")]
        with mock.patch.dict(os.environ, _home(root)):
            standing = reflect.load_standing(msgs, root / "agent")
        self.assertIn("standards/user-preferences.md", standing)
        mined = reflect.mine_transcript_messages(msgs, standing=standing)
        self.assertEqual(_prefs(mined), [])
        self.assertIn("not filed: quotes standards/user-preferences.md", _rules(mined))

    def test_load_standing_reads_claude_md_and_the_repos_agents_md(self):
        root = Path(tempfile.mkdtemp(prefix="reflect-standing-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "home" / ".claude").mkdir(parents=True)
        (root / "home" / ".claude" / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
        repo = root / "repo"
        (repo / ".claude" / "worktrees").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("Never fan out parallel implementers.\n", encoding="utf-8")
        # The session ran in a worktree that is gone by now.
        msgs = [_turn("Go.", cwd=str(repo / ".claude" / "worktrees" / "gone-slot"))]
        with mock.patch.dict(os.environ, _home(root / "home")):
            standing = reflect.load_standing(msgs)
        self.assertEqual(sorted(standing), ["AGENTS.md", "~/.claude/CLAUDE.md"])

    def test_the_trace_lists_what_was_dropped_and_why(self):
        lines = episodic_trace.mined_candidates([_turn(BRIEF), _turn("should we always rebase here?")])
        rules = [l["rule"] for l in lines]
        self.assertIn("not filed: quotes the session's first request", rules)
        self.assertIn(reflect.QUESTION_RULE, rules)


class AnIdeaInTheFirstRequestGoesToTheJudge(unittest.TestCase):
    # As typed, the session the operator opened to record it (2026-09-23); the
    # card was kept.
    OPENER = ("blog til or article idea - just enough memory injection - waht we've been working on as we "
              "perfect our memory storage system and recall that injects only what' sneeded for the model to.")

    def test_it_is_kept_and_marked(self):
        mined = reflect.mine_transcript_messages([_turn(self.OPENER), _said("Noted.")])
        self.assertEqual(len(mined["idea_candidates"]), 1)
        self.assertTrue(mined["idea_candidates"][0].from_first_request)

    def test_an_idea_said_later_is_not_marked(self):
        mined = reflect.mine_transcript_messages(
            [_turn("Look at the recall gate."), _turn("We should also add a blog post per shipped plan later.")])
        self.assertEqual([c.from_first_request for c in mined["idea_candidates"]], [False])

    def test_an_idea_quoting_claude_md_is_dropped(self):
        rule = "idea: refresh a stale branch by rebase, never by merging main back into it."
        md = CLAUDE_MD + "\n" + rule + "\n"
        mined = reflect.mine_transcript_messages([_turn("Fix the gate."), _turn(rule)],
                                                 standing={"~/.claude/CLAUDE.md": md})
        self.assertEqual(mined["idea_candidates"], [])
        self.assertIn("not filed: quotes ~/.claude/CLAUDE.md", _rules(mined))


class TheQuoteRule(unittest.TestCase):
    def test_every_long_fragment_must_appear(self):
        src = reflect.fold(CLAUDE_MD)
        self.assertTrue(reflect.quoted_in("...intent. Always refresh a stale branch by rebase, never by...", src))
        self.assertTrue(reflect.quoted_in("Ok, so. Do not append a Co-Authored-By trailer to git commit", src))
        self.assertFalse(reflect.quoted_in("Do not append a trailer, and ship the release tonight.", src))
        self.assertFalse(reflect.quoted_in("ok yes", src), "nothing four words long is no quote")
        self.assertFalse(reflect.quoted_in("Do not append a Co-Authored-By trailer", ""))

    def test_the_first_request_after_a_slash_command_is_its_arguments(self):
        cmd = _turn("<command-name>/work</command-name>\n<command-args>--name rebuild-the-idea-miner</command-args>")
        self.assertEqual(reflect.first_request_text([cmd, _turn("Then do step 2.")]), "--name rebuild-the-idea-miner")
        self.assertEqual(reflect.first_request_text(
            [_turn("task done", origin={"kind": "task-notification"}), _turn(BRIEF)]), BRIEF)


class TheAlwaysLoadReader(unittest.TestCase):
    def test_it_skips_navigation_and_a_pen_copy(self):
        root = Path(tempfile.mkdtemp(prefix="always-load-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".obsidian").mkdir()
        mem = root / "agent"
        pen = mem.joinpath(*vault_layout.LEGACY_PEN_REL)
        pen.mkdir(parents=True)
        (root / "standards").mkdir()
        for p in (root / "standards" / "a.md", root / "standards" / "moc-index.md", pen / "a.md", pen / "b.md"):
            p.write_text("x\n", encoding="utf-8")
        got = [p.relative_to(root).as_posix() for p in vault_layout.always_load_files(mem)]
        self.assertEqual(got, ["standards/a.md", pen.relative_to(root).joinpath("b.md").as_posix()])


# The release review of 2026-10-22 (task 192 step 9) found the cases below,
# each failing before its fix.

def _command(name, args=""):
    return _turn(f"<command-name>/{name}</command-name>\n<command-message>{name}</command-message>\n"
                 f"<command-args>{args}</command-args>", uuid="c0")


class ACommandTheHostRunsIsNotTheRequest(unittest.TestCase):
    """`/clear` or `/model opusplan` carries no brief: the request is what is
    pasted next, and its standing rules are a quote, not a sighting."""
    BRIEF = ("Work the plan in .harness/PLAN.md. I want every commit to run the full battery first, from now on. "
             "Use rebase not merge when refreshing a branch, going forward.")
    STDOUT = _turn("<local-command-stdout>Set model to opusplan</local-command-stdout>", uuid="c1")

    def test_the_request_after_model_is_the_brief(self):
        self.assertEqual(reflect.first_request_text(
            [_command("model", "opusplan"), self.STDOUT, _turn(self.BRIEF, uuid="u1")]), self.BRIEF)

    def test_the_request_after_clear_is_the_brief(self):
        self.assertEqual(reflect.first_request_text([_command("clear"), _turn(self.BRIEF, uuid="u1")]), self.BRIEF)

    def test_a_command_answered_by_the_host_is_skipped_whatever_its_name(self):
        stdout = _turn("<local-command-stdout>Done</local-command-stdout>", uuid="c1")
        self.assertEqual(reflect.first_request_text(
            [_command("some-new-builtin"), stdout, _turn(self.BRIEF, uuid="u1")]), self.BRIEF)

    def test_a_brief_pasted_after_model_files_no_high_card(self):
        mined = reflect.mine_transcript_messages([_command("model", "opusplan"), self.STDOUT, _turn(self.BRIEF, uuid="u1")])
        self.assertEqual([c.title for c in mined["memory_candidates"] if c.confidence == "HIGH"], [])


class TheQuestionGuardReadsContractionsAndOrders(unittest.TestCase):
    def test_a_negated_auxiliary_before_its_subject_is_a_question(self):
        for s in ("Shouldn't we use rebase instead of merge going forward",
                  "Can't you use rebase instead of merge from now on",
                  "Don't you think we should always rebase"):
            self.assertTrue(reflect.is_question(s), s)

    def test_do_it_and_have_it_are_orders(self):
        for s in ("Do it with rebase instead of merge from now on.",
                  "Have it use rebase instead of merge from now on.",
                  "Do this with rebase from now on.",
                  "Don't use merge here, from now on."):
            self.assertFalse(reflect.is_question(s), s)

    def test_the_questions_it_already_read_still_are(self):
        for s in ("Do we always rebase here", "Have you run the battery", "Does it need a rebase", "Is it worth it"):
            self.assertTrue(reflect.is_question(s), s)


class EachIdeaInAMessageIsARemark(unittest.TestCase):
    def test_two_ideas_in_one_message_are_two_remarks(self):
        msgs = [_turn("hello, let's look at the search ranking code", uuid="u0"), _said("Sure."),
                _turn("Later, we could add a dashboard for this. We should also eventually support tags.", uuid="u1")]
        remarks = [e["remark"] for e in episodic_trace.mined_ideas(msgs, "")]
        self.assertIn("Later, we could add a dashboard for this.", remarks)
        self.assertIn("We should also eventually support tags.", remarks)


class AnIdeasContextStaysBounded(unittest.TestCase):
    def test_a_run_on_message_keeps_the_context_near_its_bound(self):
        filler = " ".join(["and then the importer walks every folder"] * 40)
        text = "so " + filler + " and later we could add a cache for the folder walk " + filler
        msgs = [_turn("look at the importer", uuid="u0"), _said("x " * 600), _turn(text, uuid="u1")]
        entries = episodic_trace.mined_ideas(msgs, "")
        self.assertEqual(len(entries), 1)
        self.assertLessEqual(len(entries[0]["context"]), reflect.IDEA_CONTEXT_CHARS)
        # About REMARK_CHARS: the window widens to whole words at each end.
        self.assertLessEqual(len(entries[0]["remark"]), reflect.REMARK_CHARS + 40)
        self.assertIn("later we could add a cache for the folder walk", entries[0]["remark"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
