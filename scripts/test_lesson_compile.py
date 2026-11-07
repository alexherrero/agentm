#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/lesson_compile.py (task 199).

The night's lesson compile: per-session bookmarks over every transcript folder,
condensed slices, one compile per project with a dedupe-and-augment decision,
the auto-memory input, the agy dry run, and the caps.

No test reaches a model or the network. Every external program goes through the
module's injectable runner, and as a second wall each test puts `claude`, `agy`
and `gh` stubs first on PATH that record any call and fail; tearDown asserts none
was made. A missed injection fails loudly instead of spending. (`agentmd` stays
real: the filing engine reads the storage contract through `agentmd rules`, a
local read, as every filing test in the battery does. The compile's searches go
through the injected runner.)

Run: python3 scripts/test_lesson_compile.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SKILL_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import lesson_compile as lc  # noqa: E402
import session_binding  # noqa: E402

_NOW = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)
_HOOK = _REPO / "harness" / "hooks" / "memory-automemory-note" / "memory-automemory-note.sh"


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _assistant(text="", tools=()):
    content = [{"type": "text", "text": text}] if text else []
    for i, (name, inp) in enumerate(tools):
        content.append({"type": "tool_use", "id": f"t{i}", "name": name, "input": inp})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _tool_result(text, error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t0", "content": text, "is_error": error}]}}


def _lines(rows) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


class FakeRunner:
    """Answers `claude`, `agy`, `agentmd`, `git` and `gh` from fixtures and
    records every call."""

    def __init__(self, *, candidates=None, actions=None, search=None, cost=0.05, draft_error=False,
                 digest="The session found that X fails without Y; adding Y fixed it.", draft_text=None):
        self.draft_text = draft_text
        self.calls: list = []
        self.candidates = candidates if candidates is not None else []
        self.actions = actions if actions is not None else []
        self.search = search or {}
        self.cost = cost
        self.draft_error = draft_error
        self.digest = digest
        self.git_log = ""

    def prompts(self, kind: str) -> list:
        return [inp for argv, inp in self.calls if argv[0] == "claude" and kind in (inp or "")]

    def __call__(self, argv, input_text=None, cwd=None, timeout=60, env=None):
        self.calls.append((list(argv), input_text))
        head = argv[0]
        if head == "claude":
            if "Draft the durable lessons" in (input_text or ""):
                if self.draft_error:
                    return 1, "", "Not logged in"
                result = self.draft_text if self.draft_text is not None else json.dumps({"candidates": self.candidates})
            else:
                result = json.dumps({"actions": self.actions})
            return 0, json.dumps({"type": "result", "result": result, "total_cost_usd": self.cost,
                                  "is_error": False}) + "\n", ""
        if head == "agy":
            if argv[1:2] == ["models"]:
                return 0, ("gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"
                           "gemini-3.9-flash-medium\tGemini 3.9 Flash (Medium)\n"
                           "gemini-4-argon-high\tGemini 4 Argon (High)\n"), ""
            return 0, self.digest, ""
        if Path(head).stem == "agentmd":
            query = argv[-1]
            for key, rows in self.search.items():
                if key in query:
                    return 0, json.dumps({"results": rows}), ""
            return 0, json.dumps({"results": []}), ""
        if head == "git":
            if "log" in argv:
                return 0, self.git_log, ""
            return 0, "https://github.com/example/alpha.git\n", ""
        if head == "gh":
            return 0, json.dumps([{"number": 7, "title": "Teach the widget to wait"}]), ""
        raise AssertionError(f"unexpected program: {argv}")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        for d in ("memory/semantic", "memory/procedural", "memory/episodic"):
            (self.vault / d).mkdir(parents=True)
        self.repo = self.root / "code" / "alpha"
        self.repo.mkdir(parents=True)
        for slug, path in (("alpha", self.repo), ("beta", self.root / "code" / "beta")):
            (self.vault / "projects" / slug).mkdir(parents=True)
            (self.vault / "projects" / slug / "project.yaml").write_text(
                f"slug: {slug}\ncode_paths:\n  - {path}\n", encoding="utf-8")
        self.claude = self.root / "home" / ".claude" / "projects"
        self.claude.mkdir(parents=True)
        self.agy = self.root / "gemini" / "antigravity-cli"
        self.agy.mkdir(parents=True)
        # The second wall: real programs on PATH are stubs that record and fail.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.stub_log = self.root / "stub-calls.log"
        for name in ("claude", "agy", "gh"):
            stub = self.bin / name
            stub.write_text(f"#!/bin/sh\necho {name} >> '{self.stub_log}'\nexit 97\n", encoding="utf-8")
            stub.chmod(0o755)
        self._env = mock.patch.dict(os.environ, {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "AGENTM_CLAUDE_PROJECTS": str(self.claude),
            "AGENTM_AGY_ROOTS": str(self.agy),
            "AGENTM_INSTALL_PREFIX": str(self.root / "prefix"),
        })
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        called = self.stub_log.read_text(encoding="utf-8") if self.stub_log.exists() else ""
        self.tmp.cleanup()
        self.assertEqual(called, "", "a real program was reached past the injected runner")

    # fixtures

    def transcript(self, folder: str, sid: str, rows, *, cwd: "Path | None" = None, age_hours=0.0) -> Path:
        d = self.claude / folder
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        head = [{"type": "system", "cwd": str(cwd)}] if cwd else []
        p.write_text(_lines(head + list(rows)), encoding="utf-8")
        if age_hours:
            t = time.time() - age_hours * 3600
            os.utime(p, (t, t))
        return p

    def agy_transcript(self, sid: str, rows) -> Path:
        d = self.agy / "brain" / sid / ".system_generated" / "logs"
        d.mkdir(parents=True)
        p = d / "transcript_full.jsonl"
        p.write_text(_lines(rows), encoding="utf-8")
        return p

    def collect(self, **kw):
        return lc.collect(memory_root=self.vault, now=_NOW, **kw)

    def pending(self, project: str) -> list:
        return lc._read_jsonl(lc.pending_dir() / f"{project}.jsonl")

    def compile(self, runner, **kw):
        settings = kw.pop("settings", lc.Settings())
        now = kw.pop("now", _NOW)
        return lc.compile_night(memory_root=self.vault, runner=runner, settings=settings, now=now, **kw)

    def cards(self) -> list:
        return sorted(p for p in (self.vault / "memory").rglob("*.md") if "episodic" not in p.parts)


# ── bookmarks ─────────────────────────────────────────────────────────────────

class TestBookmarks(_Base):
    def test_a_later_pass_reads_only_what_is_new(self) -> None:
        p = self.transcript("-x", "s1", [_user("First the alpha fact."), _assistant("ok", [("Bash", {"command": "ls"})])],
                            cwd=self.repo)
        self.collect()
        first = self.pending("alpha")
        self.assertEqual(len(first), 1)
        self.assertIn("First the alpha fact", first[0]["text"])
        with p.open("a", encoding="utf-8") as f:
            f.write(_lines([_user("Then the beta fact."), _assistant("done", [("Read", {"file_path": "/a"})])]))
        self.collect()
        second = self.pending("alpha")[1]
        self.assertIn("Then the beta fact", second["text"])
        self.assertNotIn("First the alpha fact", second["text"])

    def test_nothing_new_stages_nothing(self) -> None:
        self.transcript("-x", "s1", [_user("Once."), _assistant("ok", [("Bash", {"command": "ls"})])], cwd=self.repo)
        self.collect()
        report = self.collect()
        self.assertEqual(report["staged"], 0)
        self.assertEqual(len(self.pending("alpha")), 1)

    def test_a_rewritten_transcript_is_read_from_the_top(self) -> None:
        p = self.transcript("-x", "s1", [_user("Long original turn one."), _user("Long original turn two."),
                                         _assistant("ok", [("Bash", {"command": "ls"})])], cwd=self.repo)
        self.collect()
        p.write_text(_lines([{"type": "system", "cwd": str(self.repo)}, _user("Short."),
                             _assistant("ok", [("Bash", {"command": "pwd"})])]), encoding="utf-8")
        self.collect()
        self.assertIn("Short.", self.pending("alpha")[-1]["text"])

    def test_a_line_still_being_written_waits(self) -> None:
        p = self.transcript("-x", "s1", [_user("Whole line."), _assistant("ok", [("Bash", {"command": "ls"})])],
                            cwd=self.repo)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_user("Half a line"))[:20])
        self.collect()
        self.assertNotIn("Half a line", self.pending("alpha")[0]["text"])
        marks = lc.load_bookmarks()
        self.assertEqual(marks[f"{lc.CLAUDE}:s1"]["lines"], 3)

    def test_first_run_starts_from_today(self) -> None:
        self.transcript("-old", "old", [_user("Ancient history."), _assistant("ok", [("Bash", {"command": "ls"})])],
                        cwd=self.repo, age_hours=48)
        self.transcript("-new", "new", [_user("Fresh work."), _assistant("ok", [("Bash", {"command": "ls"})])],
                        cwd=self.repo)
        report = self.collect()
        self.assertTrue(report["seeded"])
        texts = " ".join(r["text"] for r in self.pending("alpha"))
        self.assertIn("Fresh work", texts)
        self.assertNotIn("Ancient history", texts)

    def test_bookmarks_live_in_the_state_dir_and_no_marker_is_written(self) -> None:
        self.transcript("-x", "s1", [_user("Hi."), _assistant("ok", [("Bash", {"command": "ls"})])], cwd=self.repo)
        self.collect()
        self.assertTrue(lc.bookmarks_path().is_file())
        self.assertEqual(list(self.root.rglob("session-id-*")), [])

    def test_worktree_and_chip_folders_are_scanned(self) -> None:
        wt = self.repo / ".claude" / "worktrees" / "slot"
        wt.mkdir(parents=True)
        self.transcript("-wt", "w1", [_user("From a worktree."), _assistant("ok", [("Bash", {"command": "ls"})])],
                        cwd=wt)
        self.collect()
        self.assertIn("From a worktree", self.pending("alpha")[0]["text"])

    def test_a_subagent_file_is_not_a_session(self) -> None:
        sub = self.claude / "-x" / "s1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-1.jsonl").write_text(_lines([_user("Subagent prompt.")]), encoding="utf-8")
        self.assertEqual(lc.discover(self.claude, []), [])

    def test_dry_run_moves_no_mark_and_stages_nothing(self) -> None:
        self.transcript("-x", "s1", [_user("Hi."), _assistant("ok", [("Bash", {"command": "ls"})])], cwd=self.repo)
        self.collect(dry_run=True)
        self.assertIsNone(lc.load_bookmarks())
        self.assertEqual(self.pending("alpha"), [])


# ── slices ────────────────────────────────────────────────────────────────────

class TestSlices(_Base):
    def test_labels_and_no_tool_output(self) -> None:
        rows = [
            _user("Use the long flag."),
            _assistant("Running it.", [("Bash", {"command": "make test --long"})]),
            _tool_result("SECRET-TOOL-OUTPUT line one\nline two"),
            _assistant("", [("Bash", {"command": "make broken"})]),
            _tool_result("make: *** No rule to make target 'broken'", error=True),
            _user("Peer says the cache is stale.", turnOrigin="peer"),
            _assistant("Checked: the cache was stale, cleared it."),
        ]
        text, messages = lc.condense_claude(rows)
        self.assertIn("OPERATOR: Use the long flag.", text)
        self.assertIn("AGENT: Running it.", text)
        self.assertIn("  > Bash: make test --long", text)
        self.assertIn("  ! error: make: *** No rule to make target 'broken'", text)
        self.assertNotIn("SECRET-TOOL-OUTPUT", text)
        self.assertIn("RELAYED (not the operator", text)
        self.assertIn("Peer says the cache is stale.", text)
        self.assertNotIn("OPERATOR: Peer says", text)

    def test_a_scripted_exchange_is_not_a_session(self) -> None:
        self.transcript("-tr", "t1", [_user("Translate these cues.", turnOrigin="sdk", promptSource="sdk"),
                                      _assistant("Here they are.")])
        report = self.collect()
        self.assertEqual(report["staged"], 0)

    def test_an_unmatched_session_folds_into_general(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.transcript("-e", "e1", [_user("Generic work."), _assistant("ok", [("Bash", {"command": "ls"})])],
                        cwd=elsewhere)
        self.collect()
        self.assertEqual(len(self.pending(lc.GENERAL)), 1)

    def test_the_regex_miner_rides_along_as_hints(self) -> None:
        self.transcript("-x", "s1", [_user("Start the release."), _assistant("ok", [("Bash", {"command": "ls"})]),
                                     _user("I always want the changelog written before the tag."),
                                     _assistant("Noted.")], cwd=self.repo)
        self.collect()
        hints = self.pending("alpha")[0]["hints"]
        self.assertTrue(any("changelog" in h for h in hints), hints)
        self.assertEqual(self.cards(), [], "a hint is never filed")


# ── the compile ───────────────────────────────────────────────────────────────

def _cand(title="Widgets need a warm cache", lesson="A cold cache makes the widget build fail; warm it first.",
          mtype="fix", query="widget cold cache"):
    return {"title": title, "type": mtype, "lesson": lesson, "evidence": ["session s1"], "query": query}


class TestCompile(_Base):
    def stage(self, project="alpha", text="OPERATOR: the widget failed cold.\n  > Bash: make widget"):
        lc._append_jsonl(lc.pending_dir() / f"{project}.jsonl", {
            "host": lc.CLAUDE, "session": f"s-{project}", "project": project, "cwd": "",
            "collected": _NOW.isoformat(), "text": text, "hints": [], "relayed": False})

    def test_a_new_lesson_files_through_the_engine(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[_cand()], actions=[{"candidate": 1, "action": "new",
                                                            "title": "Widgets need a warm cache", "type": "fix",
                                                            "body": "A cold cache makes the widget build fail.",
                                                            "evidence": ["session s1"]}])
        out = self.compile(runner)
        (card,) = self.cards()
        text = card.read_text(encoding="utf-8")
        self.assertIn("source: conversation", text)
        self.assertIn("status: unfiled", text)
        self.assertIn("project: alpha", text)
        self.assertIn("## Evidence", text)
        self.assertEqual(out["projects"][0]["new"], [card.relative_to(self.vault).as_posix()])
        self.assertEqual(self.pending("alpha"), [], "a compiled project's slices are consumed")
        self.assertAlmostEqual(out["total_cost_usd"], 0.10)

    def test_a_quiet_day_writes_nothing_and_makes_one_call(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[])
        self.compile(runner)
        self.assertEqual(self.cards(), [])
        self.assertEqual(len(runner.prompts("deciding")), 0)

    def test_an_existing_lesson_is_augmented_not_duplicated(self) -> None:
        existing = self.vault / "memory" / "procedural" / "warm-the-widget-cache.md"
        original = ("---\ntitle: Warm the widget cache\ntype: workflow\nimportance: 8\n---\n\n"
                    "Warm the cache before building widgets.\n")
        existing.write_text(original, encoding="utf-8")
        self.stage()
        runner = FakeRunner(
            candidates=[_cand()],
            search={"widget": [{"path": "memory/procedural/warm-the-widget-cache.md", "title": "Warm the widget cache",
                                "type": "workflow", "lifecycle": "active", "summary": "warm first"}]},
            actions=[{"candidate": 1, "action": "augment", "target": "N1.1", "kind": "evidence",
                      "added": "Seen again on 2026-11-06: a cold build failed until the cache was warmed."}])
        out = self.compile(runner)
        self.assertEqual(self.cards(), [existing], "no second card")
        text = existing.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original.rstrip("\n")), "the text above the section is unchanged")
        self.assertIn("## Added by dreaming", text)
        self.assertIn("lesson compile · evidence", text)
        self.assertIn("a cold build failed", text)
        self.assertEqual(out["projects"][0]["augmented"], ["memory/procedural/warm-the-widget-cache.md"])
        decide = runner.prompts("deciding")[0]
        self.assertIn("AUGMENTABLE", decide)

    def test_a_second_augment_joins_the_same_section(self) -> None:
        card = ("---\ntitle: T\ntype: fix\n---\n\nBody.\n\n## Added by dreaming\n\n**2026-10-01 · x**\n\nEarlier.\n"
                "\n## Notes\n\nOperator text below.\n")
        out = lc.with_addition(card, "New evidence.", day="2026-11-07", kind="evidence")
        self.assertEqual(out.count("## Added by dreaming"), 1)
        self.assertLess(out.index("New evidence."), out.index("## Notes"))
        self.assertTrue(out.endswith("Operator text below.\n"))
        self.assertIsNone(lc.with_addition(out, "New evidence.", day="2026-11-08", kind="evidence"),
                          "words the card already holds are not added twice")

    def test_a_read_only_match_is_never_written(self) -> None:
        plan = self.vault / "projects" / "alpha" / "plan.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        self.stage()
        runner = FakeRunner(candidates=[_cand()],
                            search={"widget": [{"path": "projects/alpha/plan.md", "title": "Plan"}]},
                            actions=[{"candidate": 1, "action": "augment", "target": "N1.1", "added": "x"}])
        self.compile(runner)
        self.assertEqual(plan.read_text(encoding="utf-8"), "# Plan\n")
        self.assertEqual(self.cards(), [])

    def test_the_search_is_the_daemons_hybrid_over_the_whole_vault(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[_cand()], actions=[])
        self.compile(runner)
        searches = [argv for argv, _ in runner.calls if Path(argv[0]).stem == "agentmd"]
        self.assertEqual(len(searches), 1)
        argv = searches[0]
        self.assertIn("hybrid", argv)
        self.assertIn("-question", argv)
        self.assertNotIn("-project", argv, "the search is not narrowed to the project")

    def test_a_lesson_card_is_not_crowded_out_by_project_files(self) -> None:
        rows = [{"path": f"projects/alpha/lists/l{i}.md", "title": f"List {i}"} for i in range(12)]
        rows.append({"path": "memory/procedural/warm.md", "title": "Warm the cache", "type": "workflow",
                     "lifecycle": "active"})
        runner = FakeRunner(search={"widget": rows})
        ns = lc.search_neighbours("widget cache", vault_root=self.vault, memory_root=self.vault, runner=runner)
        self.assertEqual(len(ns), 6)
        self.assertEqual(ns[0].rel, "memory/procedural/warm.md")
        argv = runner.calls[0][0]
        self.assertGreaterEqual(int(argv[argv.index("-k") + 1]), 24)

    def test_at_most_five_writes_a_project(self) -> None:
        self.stage()
        topics = ["Rsync drops xattrs unless asked", "Plex needs a film refresh", "Go caches test results",
                  "Launchd jobs need an explicit kickstart", "SQLite WAL needs immutable reads",
                  "Squash merges hide ancestry", "Zsh echo eats three equals signs", "Drive mirrors in place"]
        cands = [_cand(title=t, lesson=f"{t}, which cost a morning to learn.", query=t) for t in topics]
        actions = [{"candidate": i + 1, "action": "new", "title": t, "type": "reference",
                    "body": f"{t}, which cost a morning to learn."} for i, t in enumerate(topics)]
        out = self.compile(FakeRunner(candidates=cands, actions=actions))
        self.assertEqual(len(self.cards()), 5)
        self.assertEqual(out["projects"][0]["dropped"], 3)

    def test_a_lesson_two_projects_learned_is_filed_once(self) -> None:
        self.stage("alpha")
        self.stage("beta")
        same = {"action": "new", "title": "Widgets need a warm cache", "type": "fix",
                "body": "A cold cache makes the widget build fail; warm it first."}
        runner = FakeRunner(candidates=[_cand()], actions=[dict(same, candidate=1)])
        self.compile(runner)
        self.assertEqual(len(self.cards()), 1)
        second = runner.prompts("deciding")[1]
        self.assertIn("T1 ", second, "the night's earlier lessons are shown to the next project")

    def test_the_cost_cap_stops_a_call_and_keeps_the_slices(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[_cand()])
        out = self.compile(runner, settings=lc.Settings(project_cap_usd=0.01))
        self.assertEqual([a for a, _ in runner.calls if a[0] == "claude"], [])
        self.assertEqual(out["projects"][0]["error"], "cost cap reached")
        self.assertEqual(len(self.pending("alpha")), 1, "kept for the next night")

    def test_each_call_gets_what_is_left_as_its_budget(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[_cand()], cost=0.25)
        self.compile(runner, settings=lc.Settings(project_cap_usd=0.60))
        budgets = [float(a[a.index("--max-budget-usd") + 1]) for a, _ in runner.calls if a[0] == "claude"]
        self.assertEqual(budgets, [0.60, 0.35])

    def test_spend_carries_across_runs_on_the_same_day(self) -> None:
        self.stage()
        self.compile(FakeRunner(candidates=[_cand()], cost=0.30), settings=lc.Settings(project_cap_usd=0.60))
        self.stage()
        runner = FakeRunner(candidates=[_cand()])
        out = self.compile(runner, settings=lc.Settings(project_cap_usd=0.60))
        self.assertEqual([a for a, _ in runner.calls if a[0] == "claude"], [])
        self.assertEqual(out["projects"][0]["error"], "cost cap reached")

    def test_a_failed_call_keeps_the_slices(self) -> None:
        self.stage()
        out = self.compile(FakeRunner(draft_error=True))
        self.assertIn("draft", out["projects"][0]["error"])
        self.assertEqual(len(self.pending("alpha")), 1)

    def test_an_unparseable_draft_is_an_error_not_a_quiet_day(self) -> None:
        self.stage()
        out = self.compile(FakeRunner(draft_text="I could not decide; here are some thoughts."))
        self.assertEqual(out["projects"][0]["error"], "draft: the reply held no JSON")
        self.assertEqual(len(self.pending("alpha")), 1, "kept for the next night")
        (kept,) = (lc.state_dir() / "unparsed").glob("*-alpha-draft.txt")
        self.assertIn("some thoughts", kept.read_text(encoding="utf-8"))

    def test_a_reply_about_shell_code_still_parses(self) -> None:
        # A raw newline inside a string and a backslash before `$` or `(` are
        # what strict JSON refuses; a lesson about a shell is full of both.
        raw = '{"candidates": [{"title": "Quote it", "lesson": "Use \\$HOME and \\(x\\)\nnot a pair: \\\\ ok"}]}'
        data = lc.parse_json_reply(raw)
        self.assertEqual(data["candidates"][0]["title"], "Quote it")
        self.assertIn("\\$HOME", data["candidates"][0]["lesson"])
        self.assertEqual(lc.parse_json_reply("```json\n{\"candidates\": []}\n```"), {"candidates": []})
        self.assertIsNone(lc.parse_json_reply("no object at all"))

    def test_dry_run_writes_nothing(self) -> None:
        self.stage()
        runner = FakeRunner(candidates=[_cand()], actions=[{"candidate": 1, "action": "new", "title": "T",
                                                            "type": "fix", "body": "B."}])
        out = self.compile(runner, dry_run=True)
        self.assertEqual(self.cards(), [])
        self.assertEqual(out["projects"][0]["new"], ["T"])
        self.assertEqual(len(self.pending("alpha")), 1)

    def test_the_days_commits_prs_and_traces_reach_the_draft(self) -> None:
        self.stage()
        trace = self.vault / "memory" / "episodic" / "2026-11-07-x.md"
        trace.write_text("---\ntitle: Fix the widget\nday: 2026-11-07\nproject: alpha\nsession: s\n---\n\n"
                         "## Asked\n\nFix the widget build.\n\n## Outcome\n\nWarmed the cache; green.\n",
                         encoding="utf-8")
        runner = FakeRunner(candidates=[])
        runner.git_log = "abc1234 2026-11-07 Warm the cache before the widget build\n"
        self.compile(runner)
        draft = runner.prompts("Draft the durable lessons")[0]
        self.assertIn("commit abc1234", draft)
        self.assertIn("merged PR example/alpha#7", draft)
        self.assertIn("Warmed the cache; green.", draft)

    def test_relayed_turns_carry_the_trust_rule(self) -> None:
        self.stage(text="RELAYED (not the operator; counts only if the agent checked or acted on it): X is true.")
        runner = FakeRunner(candidates=[])
        self.compile(runner)
        draft = runner.prompts("Draft the durable lessons")[0]
        self.assertIn("RELAYED (not the operator", draft)
        self.assertIn("counts only if", draft.split("=== INPUTS ===")[0])

    def test_the_claude_call_shape(self) -> None:
        argv = lc.claude_argv("sonnet", 0.6)
        self.assertEqual(argv[:4], ["claude", "-p", "--model", "sonnet"])
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--tools=", argv)
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"disableAllHooks": True})
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.60")


# ── auto-memory ───────────────────────────────────────────────────────────────

class TestAutomemory(_Base):
    def note(self, folder: str, name: str, text: str) -> Path:
        d = self.claude / folder / "memory"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_memory_write_is_queued_and_nothing_else_is(self) -> None:
        p = self.note("-x", "lesson.md", "x")
        self.assertTrue(lc.note_automemory({"tool_input": {"file_path": str(p)}}))
        self.assertFalse(lc.note_automemory({"tool_input": {"file_path": str(p.parent / "MEMORY.md")}}))
        self.assertFalse(lc.note_automemory({"tool_input": {"file_path": str(self.root / "src" / "a.md")}}))
        queue = lc._read_jsonl(lc.automemory_queue())
        self.assertEqual([q["path"] for q in queue], [str(p)])
        self.assertEqual(self.cards(), [], "the hook files nothing")

    def test_a_queued_note_reaches_its_projects_compile(self) -> None:
        folder = session_binding.host_folder_name(str(self.repo))
        p = self.note(folder, "warm-cache.md", "Warm the widget cache before a build.")
        lc.note_automemory({"tool_input": {"file_path": str(p)}})
        runner = FakeRunner(candidates=[])
        self.compile(runner)
        draft = runner.prompts("Draft the durable lessons")[0]
        self.assertIn("project `alpha`", draft)
        self.assertIn("Warm the widget cache before a build.", draft)
        self.assertEqual(lc._read_jsonl(lc.automemory_queue()), [], "consumed once compiled")

    @unittest.skipIf(os.name == "nt", "bash hook")
    def test_the_hook_queues_through_the_shell(self) -> None:
        p = self.note("-x", "lesson.md", "x")
        payload = json.dumps({"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": str(p)}})
        r = subprocess.run(["bash", str(_HOOK)], input=payload, capture_output=True, text=True,
                           env={**os.environ, "AGENTM_INSTALL_PREFIX": str(self.root / "no-prefix")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([q["path"] for q in lc._read_jsonl(lc.automemory_queue())], [str(p)])
        other = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "a.py")}})
        subprocess.run(["bash", str(_HOOK)], input=other, capture_output=True, text=True)
        self.assertEqual(len(lc._read_jsonl(lc.automemory_queue())), 1)

    def test_the_backfill_folds_every_note_once(self) -> None:
        folder = session_binding.host_folder_name(str(self.repo))
        self.note(folder, "a.md", "Note A about widgets.")
        self.note(folder, "b.md", "Note B about caches.")
        self.note(folder, "MEMORY.md", "- index")
        runner = FakeRunner(candidates=[])
        out = lc.backfill_automemory(memory_root=self.vault, runner=runner, settings=lc.Settings(), now=_NOW)
        (rep,) = out["projects"]
        self.assertEqual((rep["project"], rep["notes"]), ("alpha", 2))
        draft = runner.prompts("Draft the durable lessons")[0]
        self.assertIn("Note A about widgets.", draft)
        self.assertNotIn("- index", draft)
        again = lc.backfill_automemory(memory_root=self.vault, runner=FakeRunner(), now=_NOW)
        self.assertEqual(again["skipped"], "already run")

    def test_a_backfill_the_cap_stops_is_finished_next_time(self) -> None:
        self.note(session_binding.host_folder_name(str(self.repo)), "a.md", "Note A about widgets.")
        stopped = lc.backfill_automemory(memory_root=self.vault, runner=FakeRunner(candidates=[]),
                                         settings=lc.Settings(night_cap_usd=0.01), now=_NOW)
        self.assertEqual(stopped["projects"][0]["error"], "cost cap reached")
        runner = FakeRunner(candidates=[])
        again = lc.backfill_automemory(memory_root=self.vault, runner=runner, settings=lc.Settings(), now=_NOW)
        self.assertEqual([r["project"] for r in again["projects"]], ["alpha"])
        self.assertEqual(len(runner.prompts("Draft the durable lessons")), 1)


# ── Antigravity ───────────────────────────────────────────────────────────────

class TestAntigravity(_Base):
    ROWS = [
        {"step_index": "0", "source": "USER_EXPLICIT", "type": "USER_INPUT",
         "content": "<USER_REQUEST>\nFix the build." + " It fails on the widget step." * 30 + "\n</USER_REQUEST>"},
        {"step_index": "1", "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Looking.",
         "tool_calls": [{"name": "run_command", "args": {"CommandLine": "make", "Cwd": None}}]},
        {"step_index": "2", "source": "MODEL", "type": "GENERIC",
         "content": "The command exited with code 2.\nOutput:\nAGY-TOOL-OUTPUT"},
        {"step_index": "3", "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": "system noise"},
    ]

    def rows(self, cwd: str) -> list:
        rows = json.loads(json.dumps(self.ROWS))
        rows[1]["tool_calls"][0]["args"]["Cwd"] = cwd
        return rows

    def test_agy_rows_condense_and_map_by_their_cwd(self) -> None:
        self.agy_transcript("a1", self.rows(str(self.repo)))
        self.collect()
        (rec,) = self.pending("alpha")
        self.assertEqual(rec["host"], lc.AGY)
        self.assertIn("REQUESTER", rec["text"])
        self.assertIn("Fix the build.", rec["text"])
        self.assertIn("  > run_command: make", rec["text"])
        self.assertIn("  ! error: The command exited with code 2.", rec["text"])
        self.assertNotIn("system noise", rec["text"])
        marks = lc.load_bookmarks()
        self.assertIn(f"{lc.AGY}:a1", marks)

    def test_the_nights_own_digest_calls_are_not_read_back(self) -> None:
        # Every `agy -p` leaves a transcript. A digest call's conversation is
        # the night's own output: staging it would digest it again the next
        # night, and every night after.
        own = self.rows(str(self.repo))
        own[0]["content"] = "<USER_REQUEST>\n" + lc.AGY_DIGEST_PROMPT + "REQUESTER: Fix the build.\n</USER_REQUEST>"
        self.agy_transcript("d1", own)
        self.agy_transcript("a1", self.rows(str(self.repo)))
        self.collect()
        self.assertEqual([r["session"] for r in self.pending("alpha")], ["a1"])
        self.assertIn(f"{lc.AGY}:d1", lc.load_bookmarks())

    def test_a_newer_agy_maps_by_the_paths_it_reads(self) -> None:
        # A newer agy records no Cwd: the workspace shows only in the paths its
        # file tools read and edit. The majority project wins.
        rows = json.loads(json.dumps(self.ROWS))
        rows[1]["tool_calls"] = [
            {"name": "read_file", "args": {"Path": str(self.repo / "README.md")}},
            {"name": "edit_file", "args": {"Path": str(self.repo / "src" / "a.py"), "Target": "x"}},
            {"name": "read_file", "args": {"Path": str(self.root / "elsewhere" / "notes.md")}},
        ]
        self.agy_transcript("a3", rows)
        self.collect()
        (rec,) = self.pending("alpha")
        self.assertFalse(rec["relayed"])
        self.assertIn("  > read_file: ", rec["text"])

    def test_a_scratch_folder_conversation_is_relayed(self) -> None:
        folder = session_binding.host_folder_name(str(self.repo))
        self.agy_transcript("a2", self.rows(f"/private/tmp/claude-501/{folder}/sess/scratchpad"))
        self.collect()
        (rec,) = self.pending("alpha")
        self.assertTrue(rec["relayed"])
        self.assertIn("RELAYED", rec["text"])

    def test_dry_until_enabled(self) -> None:
        self.agy_transcript("a1", self.rows(str(self.repo)))
        self.collect()
        runner = FakeRunner(candidates=[])
        out = self.compile(runner)
        self.assertEqual(runner.prompts("Draft the durable lessons"), [], "no compile from agy while dry")
        self.assertEqual(out["agy_digests"], 1)
        (dry,) = lc._read_jsonl(lc.state_dir() / "agy-dry" / f"{out['day']}.jsonl")
        self.assertEqual(dry["model"], "Gemini 3.9 Flash (Medium)", "the newest Flash agy serves")
        self.assertIn("adding Y fixed it", dry["digest"])
        digest_call = next(a for a, _ in runner.calls if a[:2] == ["agy", "-p"])
        self.assertIn("Fix the build.", digest_call[2])
        self.assertEqual(self.cards(), [])

    def test_enabled_feeds_the_digest_to_the_compile(self) -> None:
        self.agy_transcript("a1", self.rows(str(self.repo)))
        self.collect()
        runner = FakeRunner(candidates=[])
        self.compile(runner, settings=lc.Settings(agy_enabled=True))
        draft = runner.prompts("Draft the durable lessons")[0]
        self.assertIn("adding Y fixed it", draft)
        self.assertNotIn("AGY-TOOL-OUTPUT", draft)

    def test_a_probe_is_dropped_without_a_digest(self) -> None:
        rows = [{"step_index": "0", "source": "USER_EXPLICIT", "type": "USER_INPUT",
                 "content": "Reply with the single word ok."},
                {"step_index": "1", "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "ok",
                 "tool_calls": [{"name": "run_command", "args": {"CommandLine": "true", "Cwd": str(self.repo)}}]}]
        self.agy_transcript("p1", rows)
        self.collect()
        runner = FakeRunner(candidates=[])
        out = self.compile(runner, settings=lc.Settings(agy_enabled=True))
        self.assertEqual([a for a, _ in runner.calls if a[0] == "agy"], [])
        self.assertEqual(out["agy_digests"], 0)
        self.assertEqual(self.pending("alpha"), [])

    def test_past_the_nightly_limit_agy_slices_wait(self) -> None:
        for i in range(3):
            self.agy_transcript(f"n{i}", self.rows(str(self.repo)))
        self.collect()
        with mock.patch.object(lc, "AGY_NIGHT_LIMIT", 2):
            self.compile(FakeRunner(candidates=[]), settings=lc.Settings(agy_enabled=True))
        self.assertEqual(len(self.pending("alpha")), 1, "the third waits for the next night")

    def test_a_failed_digest_waits_for_the_next_night(self) -> None:
        self.agy_transcript("t1", self.rows(str(self.repo)))
        self.collect()

        class TimingOut(FakeRunner):
            def __call__(self, argv, input_text=None, cwd=None, timeout=60, env=None):
                if argv[:2] == ["agy", "-p"]:
                    self.calls.append((list(argv), input_text))
                    return 0, "", "error: print timeout after 3m0s with the turn still in progress"
                return super().__call__(argv, input_text, cwd, timeout, env)

        self.compile(TimingOut(candidates=[]), settings=lc.Settings(agy_enabled=True))
        (kept,) = self.pending("alpha")
        self.assertEqual(kept["session"], "t1")
        runner = FakeRunner(candidates=[])
        self.compile(runner, settings=lc.Settings(agy_enabled=True),
                     now=_NOW + timedelta(days=1))
        self.assertIn("adding Y fixed it", runner.prompts("Draft the durable lessons")[0])
        self.assertEqual(self.pending("alpha"), [])

    def test_a_nothing_durable_digest_is_not_retried(self) -> None:
        self.agy_transcript("t2", self.rows(str(self.repo)))
        self.collect()
        self.compile(FakeRunner(candidates=[], digest="NOTHING DURABLE"), settings=lc.Settings(agy_enabled=True))
        self.assertEqual(self.pending("alpha"), [])

    def test_the_digest_call_turns_recall_off(self) -> None:
        seen = {}

        def runner(argv, input_text=None, cwd=None, timeout=60, env=None):
            seen["env"] = env
            return 0, "digest", ""
        lc.digest_agy("x", model="m", runner=runner)
        self.assertEqual(seen["env"]["AGENTM_AGY_RECALL"], "off")


from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
