#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/field_brief.py — the weekly
field brief (task 185 step 3).

Every outside thing is faked: `claude -p`, the last30days engine, the web and
the mailer never run here. The runner is a recording fake that answers `claude`
with a canned stream-json run and anything else with canned engine output, so a
test can assert what the model was *told* (the prompt, the settings, the
working directory) as well as what the script did with the reply.

The bar, written before the code:

  1. The prompt carries the roadmap's What remains, the operator's file, the
     open designs and the addresses already shown.
  2. A seen address is dropped, and an address shown once is not shown again.
  3. The note has the registered kind, a keep box per item, and never more
     than ten items.
  4. `--ask` writes neither the note nor the seen-list.
  5. `keep` captures once and never twice.
  6. A run that called a tool outside its set, or started a hook, writes
     nothing.
  7. The note is mailed once, a failed send is retried without another model
     run, an absent mail path is logged and not an error, and --ask and a
     refused run never mail.

No test reaches the real mail path. The first version of the mail step was
written with the old tests unchanged, passed no mailer, and the run sent two
real emails through the operator's relay with test content. `Base.setUp` now
gives every test a recording fake mailer, a temp mailed-state file, a temp
engine-state directory and install prefix, and makes `smtplib` itself raise, so
a test that wanders onto a socket fails where it stands.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import field_brief as fb  # noqa: E402

try:  # the gates parse frontmatter as YAML; so does this, when it can
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

_TODAY = date(2026, 10, 4)

ROADMAP = """# Roadmap

## What remains (read this first)

Two arcs are in flight.

### 1. Sync-friendly storage
Task 184 tests spreading activation and MMR against the shipped search.

## The shape

not this
"""

PREFS = """# Field brief

## Topics

- agent memory
- agent skills

## Favoured sources

- Anthropic engineering blog

## Ignored sources

- (none yet)
- spamblog.example
"""

DESIGN = """---
title: vault-storage — design
status: proposed
kind: design
---

# Vault storage

## Context

### Objective

Make the vault's storage sync-friendly without losing the audit trail.
"""

ENGINE_OUT = """🌐 last30days v3.3.2 · synced 2026-10-04

# last30days v3.3.2: agent memory

- Sources: 2 active (Hacker News, Reddit)

### 1. OKF Agent Memory
   - URL: https://github.com/okf-memory/okf-agent-memory
"""


def _item(n, url=None, **kw):
    d = {"url": url or f"https://example.com/post-{n}", "title": f"Post {n}",
         "what": f"It is thing {n}. It does a thing.", "why_it_matters": f"Task 184 could use thing {n}.",
         "source": "web search", "verified": True}
    d.update(kw)
    return d


def stream(items=None, *, reply=None, cost=1.08, tools=(("WebSearch", 5), ("WebFetch", 4)),
           denials=(), refused_ids=(), available=("WebSearch", "WebFetch"), no_result=False,
           events=(), is_error=False, subtype="success", model="claude-sonnet-5",
           init_model=True, haiku_share=0.1):
    """A canned `--output-format stream-json` run. The init event names the
    session's model and its tools, as the real one does; `denials` are tool names
    every use of which the permission layer refused (an error result per call, and a
    `permission_denials` row carrying the call's id); `refused_ids` are single
    calls that errored without a denial row (a tool the run did not have);
    `haiku_share` is the fraction of the cost billed to the page summariser behind
    `WebFetch`."""
    text = reply if reply is not None else json.dumps({"items": items or []})
    lines = [json.dumps({"type": "system", "subtype": "init", "tools": list(available),
                         **({"model": model} if init_model else {})})]
    denied, errored = [], list(refused_ids)
    for name, count in tools:
        for i in range(count):
            cid = f"{name}{i}"
            lines.append(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": cid, "name": name, "input": {}}]}}))
            if name in denials:
                denied.append({"tool_name": name, "tool_use_id": cid})
                errored.append(cid)
    for cid in errored:
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": cid, "is_error": True, "content": "refused"}]}}))
    lines += [json.dumps(e) for e in events]
    if not no_result:
        lines.append(json.dumps({
            "type": "result", "subtype": subtype, "is_error": is_error, "result": text,
            "total_cost_usd": cost, "duration_ms": 150000, "num_turns": 16,
            "modelUsage": {model: {"costUSD": cost * (1 - haiku_share)},
                           "claude-haiku-4-5-20251001": {"costUSD": cost * haiku_share}},
            "permission_denials": denied}))
    return "\n".join(lines)


class FakeMailer:
    """Stands in for `session_email.send`. Records every message; never sends."""

    def __init__(self, result=None):
        self.result = result or {"sent": True, "configured": True, "relay_reply": "fake-relay-id",
                                 "skipped": None}
        self.sent = []

    def __call__(self, subject, body):
        self.sent.append({"subject": subject, "body": body})
        return dict(self.result)


class FakeRunner:
    """Answers `claude` with a canned stream and the engine with canned text."""

    def __init__(self, claude_out, engine_out=ENGINE_OUT, engine_code=0):
        self.claude_out, self.engine_out, self.engine_code = claude_out, engine_out, engine_code
        self.calls = []

    def __call__(self, argv, input_text, cwd, timeout):
        self.calls.append({"argv": list(argv), "input": input_text, "cwd": cwd})
        if argv and argv[0] == "claude":
            return 0, self.claude_out, ""
        return self.engine_code, self.engine_out, ""

    @property
    def claude(self):
        return [c for c in self.calls if c["argv"][0] == "claude"]

    @property
    def engine(self):
        return [c for c in self.calls if c["argv"][0] != "claude"]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # A nested vault: the memory root is `agent/`, the vault root its parent,
        # and `projects/` and `resources/` sit beside it.
        self.vault_root = root / "Vault"
        self.vault = self.vault_root / "agent"
        (self.vault / "memory").mkdir(parents=True)
        (self.vault_root / ".obsidian").mkdir()
        desk = self.vault_root / "projects" / "agentm" / "desk"
        docs = self.vault_root / "projects" / "agentm" / "docs"
        desk.mkdir(parents=True)
        docs.mkdir(parents=True)
        (desk / "field-brief.md").write_text(PREFS, encoding="utf-8")
        (docs / "roadmap.md").write_text(ROADMAP, encoding="utf-8")
        self.designs = root / "designs"
        self.designs.mkdir()
        (self.designs / "vault-storage.md").write_text(DESIGN, encoding="utf-8")
        (self.designs / "closed.md").write_text(
            DESIGN.replace("proposed", "launched").replace("vault-storage", "closed-one"), encoding="utf-8")
        self.state = root / "state" / "seen.jsonl"
        engine = root / "last30days.py"
        engine.write_text("# stand-in for the engine script\n", encoding="utf-8")
        self.mailer = FakeMailer()
        self.mailed = root / "state" / "mailed.jsonl"

        def _no_socket(*a, **k):
            raise AssertionError("a test reached a real SMTP connection")

        patches = [
            mock.patch.dict("os.environ", {
                "LAST30DAYS_SCRIPT": str(engine),
                # Anything that resolves state or the mail config lands in this
                # test's own directory, and finds no mail path.
                "AGENTM_STATE_DIR": str(root / "engine-state"),
                "AGENTM_INSTALL_PREFIX": str(root / "install-prefix"),
                # The capture door's lock directories land here, not in the
                # operator's ~/.cache, where earlier runs left tens of thousands.
                "XDG_CACHE_HOME": str(root / "cache")}),
            mock.patch.object(fb, "MAIL_RETRY_DELAYS", (0.0, 0.0)),
            mock.patch.object(fb, "find_python", return_value="/usr/bin/python3.12"),
            mock.patch.object(fb, "load_mailer", return_value=self.mailer),
            mock.patch.object(fb, "mailed_path", return_value=self.mailed),
            mock.patch("smtplib.SMTP", side_effect=_no_socket),
            mock.patch("smtplib.SMTP_SSL", side_effect=_no_socket),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_brief(self, runner, **kw):
        kw.setdefault("today", _TODAY)
        kw.setdefault("designs_dir", self.designs)
        kw.setdefault("state_path", self.state)
        kw.setdefault("mailer", self.mailer)
        kw.setdefault("mailed_state", self.mailed)
        return fb.run_brief(self.vault, runner=runner, **kw)

    def note(self):
        return fb.note_path(self.vault, _TODAY)


class CanonicalUrlTests(unittest.TestCase):
    def test_one_paper_is_one_address(self):
        a = fb.canonical_url("http://www.arxiv.org/pdf/2601.02744v2.pdf?utm_source=x#frag")
        b = fb.canonical_url("https://arxiv.org/abs/2601.02744")
        self.assertEqual(a, b)
        self.assertEqual(a, "https://arxiv.org/abs/2601.02744")

    def test_tracking_parameters_and_trailing_slash_do_not_make_a_page_new(self):
        self.assertEqual(fb.canonical_url("https://Example.com/a/?utm_campaign=z&b=1&a=2&fbclid=q"),
                         fb.canonical_url("https://example.com/a?a=2&b=1"))

    def test_a_release_is_a_new_address(self):
        self.assertNotEqual(fb.canonical_url("https://github.com/o/r/releases/tag/v1.0"),
                            fb.canonical_url("https://github.com/o/r/releases/tag/v1.1"))

    def test_only_web_links_have_an_address(self):
        for bad in ("javascript:alert(1)", "ftp://x/y", "not a url", "", None):
            self.assertEqual(fb.canonical_url(bad), "")


class SeenListTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "field-brief" / "seen.jsonl"

    def test_a_shown_item_is_remembered_for_ninety_days_and_not_longer(self):
        fb.record_seen(self.path, ["https://day90.example/"], today=date(2026, 7, 6))   # 90 days before
        fb.record_seen(self.path, ["https://day89.example/"], today=date(2026, 7, 7))   # 89 days before
        fb.record_seen(self.path, ["https://b.example/y"], today=date(2026, 10, 1))
        seen = fb.load_seen(self.path, today=date(2026, 10, 4))
        self.assertNotIn("https://day90.example/", seen)  # free again on the 90th day
        self.assertIn("https://day89.example/", seen)     # still withheld on the 89th
        self.assertIn("https://b.example/y", seen)

    def test_a_line_that_does_not_parse_is_skipped_not_fatal(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('not json\n{"url": "https://ok.example/", "shown": "2026-10-01"}\n{"no": 1}\n',
                             encoding="utf-8")
        self.assertEqual(list(fb.load_seen(self.path, today=date(2026, 10, 4))), ["https://ok.example/"])

    def test_a_missing_file_is_an_empty_list(self):
        self.assertEqual(fb.load_seen(self.path, today=date(2026, 10, 4)), {})


class ReadersTests(Base):
    def test_prefs_read_the_operators_file_and_skip_the_placeholder(self):
        p = fb.read_prefs(self.vault)
        self.assertEqual(p.topics, ["agent memory", "agent skills"])
        self.assertEqual(p.favoured, ["Anthropic engineering blog"])
        self.assertEqual(p.ignored, ["spamblog.example"])  # "(none yet)" held a place

    def test_prefs_default_when_the_file_is_absent(self):
        (self.vault_root / "projects" / "agentm" / "desk" / "field-brief.md").unlink()
        self.assertEqual(tuple(fb.read_prefs(self.vault).topics), fb.DEFAULT_TOPICS)

    def test_remains_is_the_section_and_stops_at_the_next(self):
        r = fb.read_remains(self.vault)
        self.assertIn("Task 184", r)
        self.assertNotIn("not this", r)

    def test_open_designs_skip_the_launched_and_carry_the_objective(self):
        ds = fb.read_open_designs(self.designs)
        self.assertEqual([d["title"] for d in ds], ["vault-storage — design"])
        self.assertIn("sync-friendly", ds[0]["objective"])


class PromptTests(Base):
    def test_the_prompt_carries_what_remains_the_operators_file_and_the_open_designs(self):
        fb_runner = FakeRunner(stream([_item(1)]))
        self.run_brief(fb_runner)
        prompt = fb_runner.claude[0]["input"]
        self.assertIn("Task 184 tests spreading activation", prompt)   # What remains
        self.assertIn("Anthropic engineering blog", prompt)            # the operator's file
        self.assertIn("vault-storage — design (proposed)", prompt)     # an open design
        self.assertNotIn("closed-one", prompt)                         # a launched one is not
        self.assertIn("OKF Agent Memory", prompt)                      # the engine's evidence
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("Do not ask any", prompt)

    def test_the_prompt_names_what_was_already_shown(self):
        fb.record_seen(self.state, ["https://example.com/already"], today=date(2026, 10, 1))
        r = FakeRunner(stream([_item(1)]))
        self.run_brief(r)
        self.assertIn("https://example.com/already", r.claude[0]["input"])


class RunSettingsTests(Base):
    def test_the_model_gets_two_tools_hooks_off_and_a_scratch_directory(self):
        r = FakeRunner(stream([_item(1)]))
        self.run_brief(r)
        argv = r.claude[0]["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertTrue(settings["disableAllHooks"])
        self.assertEqual(settings["permissions"]["allow"], ["WebSearch", "WebFetch"])
        for tool in ("Bash", "Write", "Skill", "Monitor",
                     "Agent"):  # root-casing: Claude Code's tool name, not a root space
            self.assertIn(tool, settings["permissions"]["deny"])
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--include-hook-events", argv)       # so the audit can see one
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        # From the repo, claude loads CLAUDE.md and the memory index; the run
        # that proved it cited two private notes in a brief.
        cwd = Path(r.claude[0]["cwd"])
        self.assertTrue(cwd.name.startswith("field-brief-"))
        self.assertNotIn(str(_REPO), str(cwd))
        self.assertFalse(cwd.exists(), "the scratch directory is removed")

    def test_deep_routes_to_the_strong_tier(self):
        r = FakeRunner(stream([_item(1)], model="claude-opus-5"))
        self.run_brief(r, deep=True)
        argv = r.claude[0]["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "opus")

    def test_the_engine_is_run_by_the_script_with_a_plan_built_from_the_topics(self):
        r = FakeRunner(stream([_item(1)]))
        self.run_brief(r)
        argv = r.engine[0]["argv"]
        plan = json.loads(argv[argv.index("--plan") + 1])
        self.assertEqual([s["search_query"] for s in plan["subqueries"]], ["agent memory", "agent skills"])
        self.assertIn("--emit=compact", argv)
        # One plain topic, never "a / b": the engine reads that as a comparison.
        self.assertEqual(argv[2], "agent memory")
        # and the model is never handed a way to run it
        self.assertNotIn("Bash", json.loads(r.claude[0]["argv"][r.claude[0]["argv"].index("--settings") + 1])
                         ["permissions"]["allow"])


class WeeklyNoteTests(Base):
    def test_a_weekly_run_writes_the_note_with_the_registered_kind(self):
        r = FakeRunner(stream([_item(n) for n in range(1, 10)]))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_OK)
        text = self.note().read_text(encoding="utf-8")
        self.assertEqual(self.note(), self.vault_root / "resources" / "briefs" / "2026-10-04-field-brief.md")
        fm = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        self.assertIn("kind: brief", fm)
        self.assertNotIn("type:", fm)            # a record carries kind, never type
        self.assertIn("cost_usd: 1.08", fm)
        self.assertIn("question: ", fm)
        self.assertEqual(text.count("- [ ] keep"), 9)   # one keep box per item
        self.assertIn("last30days engine (Hacker News, Reddit)", text)
        self.assertIn("5 queries", text)
        # the kind is one the contract registers, read from the shipped contract
        contract = (_REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md").read_text(encoding="utf-8")
        kinds = re.search(r"^record_kinds:\n((?:  - .*\n)+)", contract, re.M).group(1)
        self.assertIn("  - brief\n", kinds)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_the_frontmatter_parses_as_yaml_whatever_the_model_calls_itself(self):
        # The model's name comes from the run's own output and goes into the
        # frontmatter. A name with a newline and a closing fence would end the
        # block early and add keys of its own; the gates read this as YAML.
        hostile = "claude-x\n---\nkind: capture\ntags: [evil]\nmodel: \"y"
        self.run_brief(FakeRunner(stream([_item(1)], model=hostile)))
        text = self.note().read_text(encoding="utf-8")
        fm = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        data = yaml.safe_load(fm)
        self.assertEqual(data["kind"], "brief")
        self.assertEqual(data["tags"], ["field-brief"])
        self.assertEqual(data["model"], "claude-x---kind:capturetags:evilmodel:y")
        self.assertIsInstance(data["question"], str)
        self.assertEqual(data["cost_usd"], 1.08)
        self.assertEqual(text.count("\n---\n"), 1, "the only fence after the opening one is the real one")

    def test_never_more_than_ten_items(self):
        r = FakeRunner(stream([_item(n) for n in range(1, 15)]))
        out = self.run_brief(r)
        self.assertEqual(out.record["items"], 10)
        self.assertEqual(self.note().read_text(encoding="utf-8").count("- [ ] keep"), 10)

    def test_a_seen_address_is_dropped_and_the_shown_ones_are_recorded(self):
        fb.record_seen(self.state, ["https://example.com/post-2"], today=date(2026, 10, 1))
        r = FakeRunner(stream([_item(1), _item(2), _item(3)]))
        out = self.run_brief(r)
        text = self.note().read_text(encoding="utf-8")
        self.assertNotIn("post-2", text)
        self.assertIn("post-1", text)
        self.assertEqual(out.record["repeats_dropped"], 1)
        seen = fb.load_seen(self.state, today=_TODAY)
        self.assertEqual(sorted(seen), ["https://example.com/post-1", "https://example.com/post-2",
                                        "https://example.com/post-3"])
        self.assertEqual(seen["https://example.com/post-1"], "2026-10-04")

    def test_an_item_shown_this_week_is_not_shown_next_week(self):
        self.run_brief(FakeRunner(stream([_item(1), _item(2)])))
        nxt = date(2026, 10, 11)
        r = FakeRunner(stream([_item(1), _item(2), _item(3)]))
        out = self.run_brief(r, today=nxt)
        text = fb.note_path(self.vault, nxt).read_text(encoding="utf-8")
        self.assertEqual(text.count(fb._BOX_OPEN), 1)
        self.assertIn("post-3", text)
        self.assertEqual(out.record["repeats_dropped"], 2)

    def test_a_second_run_the_same_day_does_not_buy_a_second_brief(self):
        self.run_brief(FakeRunner(stream([_item(1)])))
        r = FakeRunner(stream([_item(2)]))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(r.calls, [], "nothing was run")
        self.assertEqual(out.record["total_cost_usd"], 0.0)

    def test_an_engine_that_ran_without_a_sources_line_is_still_named(self):
        r = FakeRunner(stream([_item(1)]), engine_out="# last30days\n\n### 1. A thing\n   - URL: https://x.example/\n")
        self.run_brief(r)
        text = self.note().read_text(encoding="utf-8")
        self.assertIn("last30days engine", text)
        self.assertNotIn("no social layer", text)

    def test_the_header_says_so_when_the_engine_is_unavailable(self):
        r = FakeRunner(stream([_item(1)]), engine_out="", engine_code=1)
        self.run_brief(r)
        self.assertIn("no social layer (the last30days engine exited 1)",
                      self.note().read_text(encoding="utf-8"))


class MailTests(Base):
    def test_the_note_is_mailed_once_with_its_own_text(self):
        out = self.run_brief(FakeRunner(stream([_item(1), _item(2)])))
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(len(self.mailer.sent), 1)
        msg = self.mailer.sent[0]
        self.assertEqual(msg["subject"], "Field brief — week of 2026-10-04")
        self.assertIn("post-1", msg["body"])
        self.assertIn("post-2", msg["body"])
        self.assertNotIn("kind: brief", msg["body"])        # no frontmatter in an email
        self.assertNotIn("- [ ] keep", msg["body"])          # the boxes only work in the note
        self.assertNotIn("- [x] keep", msg["body"])
        self.assertIn("keep 2026-10-04 <item number>", msg["body"])
        self.assertTrue(out.record["mail"]["sent"])
        self.assertEqual(out.record["mail"]["relay_reply"], "fake-relay-id")
        self.assertTrue(fb.already_mailed(self.mailed, "2026-10-04-field-brief.md"))

    def test_a_second_run_the_same_day_mails_nothing_and_runs_no_model(self):
        self.run_brief(FakeRunner(stream([_item(1)])))
        r = FakeRunner(stream([_item(2)]))
        out = self.run_brief(r)
        self.assertEqual(r.calls, [])
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(out.record["mail"]["skipped"], "already mailed")

    def test_a_failed_send_keeps_the_note_exits_nonzero_and_is_retried_without_the_model(self):
        self.mailer.result = {"sent": False, "configured": True, "relay_reply": None,
                              "skipped": "the relay refused the message or did not answer"}
        first = self.run_brief(FakeRunner(stream([_item(1)])))
        self.assertEqual(first.code, 8)      # literal: a non-zero exit is what the runner's watchdog counts
        self.assertNotEqual(first.code, fb.EXIT_OK)
        self.assertEqual(len(self.mailer.sent), 3, "one try and two retries before it gave up")
        self.assertTrue(self.note().is_file(), "the week's note is kept")
        self.assertFalse(fb.already_mailed(self.mailed, self.note().name))
        # the relay comes back: the same day's run sends the note and buys no second brief
        self.mailer.result = {"sent": True, "configured": True, "relay_reply": "id-2", "skipped": None}
        r = FakeRunner(stream([_item(9)]))
        second = self.run_brief(r)
        self.assertEqual(second.code, fb.EXIT_OK)
        self.assertEqual(r.calls, [], "no model run was bought for the retry")
        self.assertEqual(second.record["mail"]["relay_reply"], "id-2")
        self.assertEqual(len(self.mailer.sent), 4)

    def test_an_absent_mail_path_is_a_logged_skip_not_an_error(self):
        self.mailer.result = {"sent": False, "configured": False, "relay_reply": None,
                              "skipped": "no mail path configured (plugins.autonomy.email_to and email_smtp_url)"}
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = fb.main(["--vault-path", str(self.vault)], runner=FakeRunner(stream([_item(1)])),
                           mailer=self.mailer)
        self.assertEqual(code, fb.EXIT_OK)
        written = fb.note_path(self.vault, date.today())   # main() runs on the real day
        self.assertTrue(written.is_file())
        self.assertIn("mail not sent: no mail path configured", err.getvalue())   # logged, not silent
        self.assertFalse(fb.already_mailed(self.mailed, written.name))

    def test_no_mail_writes_the_note_and_never_calls_the_mailer(self):
        out = self.run_brief(FakeRunner(stream([_item(1)])), mail=False)
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual(out.record["mail"]["skipped"], "--no-mail")
        self.assertTrue(self.note().is_file())

    def test_ask_and_refused_runs_never_mail(self):
        self.run_brief(FakeRunner(stream([_item(1)])), ask="q")
        self.run_brief(FakeRunner(stream([_item(1)], tools=(("Bash", 1),))))                      # audit
        self.run_brief(FakeRunner(stream(reply="no json here")), today=date(2026, 10, 5))          # parse
        self.assertEqual(self.mailer.sent, [])

    def test_a_missing_mail_module_is_a_logged_skip(self):
        with mock.patch.object(fb, "load_mailer", return_value=None):
            out = fb.run_brief(self.vault, runner=FakeRunner(stream([_item(1)])), today=_TODAY,
                               designs_dir=self.designs, state_path=self.state,
                               mailed_state=self.mailed)
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertIn("not in this checkout", out.record["mail"]["skipped"])

    def test_the_guard_holds_a_test_cannot_open_a_real_connection(self):
        import smtplib
        with self.assertRaises(AssertionError):
            smtplib.SMTP("localhost", 25)
        with self.assertRaises(AssertionError):
            smtplib.SMTP_SSL("localhost", 465)


class LoadMailerTests(unittest.TestCase):
    def test_the_real_module_is_found_and_exposes_send_without_sending_anything(self):
        send = fb.load_mailer()
        self.assertTrue(callable(send))
        self.assertEqual(send.__module__, "field_brief_session_email")


class AskTests(Base):
    def test_ask_writes_neither_the_note_nor_the_seen_list(self):
        r = FakeRunner(stream([_item(1), _item(2)]))
        out = self.run_brief(r, ask="what is new in agent memory this month?")
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertFalse(self.note().exists())
        self.assertFalse(self.note().parent.exists())
        self.assertFalse(self.state.exists())
        self.assertIn("post-1", out.text)
        self.assertIn("what is new in agent memory this month?", r.claude[0]["input"])

    def test_ask_repeats_nothing_the_weekly_note_already_showed(self):
        self.run_brief(FakeRunner(stream([_item(1)])))
        r = FakeRunner(stream([_item(1), _item(5)]))
        out = self.run_brief(r, ask="anything new?", today=date(2026, 10, 5))
        self.assertNotIn("post-1", out.text)
        self.assertIn("post-5", out.text)
        self.assertIn("https://example.com/post-1", r.claude[0]["input"])  # and the model was told

    def test_ask_with_nothing_new_says_so(self):
        self.run_brief(FakeRunner(stream([_item(1)])))
        out = self.run_brief(FakeRunner(stream([_item(1)])), ask="q", today=date(2026, 10, 5))
        self.assertIn("Nothing new", out.text)


class AuditTests(Base):
    def test_a_tool_outside_the_set_refuses_the_run_and_writes_nothing(self):
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 2), ("Bash", 1))))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_AUDIT)
        self.assertIn("Bash", out.message)
        self.assertFalse(self.note().exists())
        self.assertFalse(self.state.exists())

    def test_a_hook_that_started_refuses_the_run(self):
        r = FakeRunner(stream([_item(1)], events=({"type": "system", "subtype": "hook_started"},)))
        self.assertEqual(self.run_brief(r).code, fb.EXIT_AUDIT)

    def test_a_tool_the_settings_denied_is_the_control_working_not_a_violation(self):
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 2), ("Bash", 1)), denials=("Bash",)))
        self.assertEqual(self.run_brief(r).code, fb.EXIT_OK)

    def test_tool_search_is_tolerated(self):
        r = FakeRunner(stream([_item(1)], tools=(("ToolSearch", 1), ("WebSearch", 3))))
        self.assertEqual(self.run_brief(r).code, fb.EXIT_OK)


class FailureTests(Base):
    def test_a_lapsed_login_is_named_and_leaves_no_note(self):
        r = FakeRunner(stream(reply="Failed to authenticate: OAuth session expired and could not be refreshed",
                              is_error=True, cost=0.0, tools=()))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_LOGIN)
        self.assertIn("/login", out.message)
        self.assertFalse(self.note().exists())

    def test_the_budget_cap_is_named(self):
        r = FakeRunner(stream(reply="stopped", is_error=True, subtype="error_max_budget_usd", cost=4.0))
        self.assertEqual(self.run_brief(r).code, fb.EXIT_BUDGET)

    def test_a_reply_with_no_json_writes_nothing_and_records_nothing_as_seen(self):
        out = self.run_brief(FakeRunner(stream(reply="Want this as a published page?")))
        self.assertEqual(out.code, fb.EXIT_PARSE)
        self.assertFalse(self.note().exists())
        self.assertFalse(self.state.exists())

    def test_the_cost_is_reported_even_when_the_run_fails(self):
        out = self.run_brief(FakeRunner(stream(reply="no json here", cost=0.77)))
        self.assertEqual(out.record["total_cost_usd"], 0.77)


class ModelLabelTests(unittest.TestCase):
    def test_the_model_is_the_sessions_own_even_when_the_page_summariser_costs_more(self):
        # The first live --ask billed more to Haiku (WebFetch's summariser) than to
        # the model that wrote the brief, and the record named Haiku.
        run = fb.parse_stream(stream([_item(1)], model="claude-sonnet-5-5", haiku_share=0.6))
        self.assertEqual(run.model, "claude-sonnet-5-5")

    def test_without_an_init_model_the_costliest_is_the_fallback(self):
        run = fb.parse_stream(stream([_item(1)], model="claude-sonnet-5-5", init_model=False))
        self.assertEqual(run.model, "claude-sonnet-5-5")


class ParseItemsTests(unittest.TestCase):
    def test_a_fenced_reply_with_prose_around_it_parses(self):
        text = "Here you go:\n```json\n" + json.dumps({"items": [_item(1)]}) + "\n```\nHope that helps."
        items, dropped = fb.parse_items(text)
        self.assertEqual([i.title for i in items], ["Post 1"])
        self.assertEqual(dropped, 0)

    def test_a_wikilink_a_bracket_and_a_control_character_do_not_reach_the_note(self):
        items, _ = fb.parse_items(json.dumps({"items": [_item(
            1, title="A [bad] title", what="Cites [[feedback_private-note]] twice.\x00 Fine.",
            why_it_matters="Why\nit matters.")]}))
        it = items[0]
        self.assertNotIn("[[", it.what)
        self.assertIn("feedback_private-note", it.what)    # the words stay; the link does not
        self.assertNotIn("\x00", it.what)
        self.assertNotIn("\n", it.why)
        self.assertNotIn("[", it.title)

    def test_malformed_and_repeated_items_are_dropped_and_counted(self):
        raw = {"items": [_item(1), _item(1, title="again"), {"url": "javascript:x", "title": "t",
                                                           "what": "w", "why_it_matters": "y"},
                         {"title": "no url"}, "nonsense", _item(2, verified=False)]}
        items, dropped = fb.parse_items(json.dumps(raw))
        self.assertEqual([i.url for i in items], ["https://example.com/post-1", "https://example.com/post-2"])
        self.assertEqual(dropped, 4)
        self.assertFalse(items[1].verified)

    def test_an_unread_page_is_marked_so_in_the_note(self):
        items, _ = fb.parse_items(json.dumps({"items": [_item(1, verified=False)]}))
        self.assertIn("Not read in full.", fb.render_items(items))


class KeepTests(Base):
    def _write_note(self, n=3):
        self.run_brief(FakeRunner(stream([_item(i) for i in range(1, n + 1)])))
        return self.note()

    def test_keep_captures_the_item_once_and_ticks_its_box(self):
        note = self._write_note()
        out = fb.keep_item(self.vault, note, 2, "task 184 needs this baseline")
        self.assertEqual(out.code, fb.EXIT_OK, out.message)
        card = out.path
        self.assertTrue(card.is_file())
        body = card.read_text(encoding="utf-8")
        self.assertIn("task 184 needs this baseline", body)         # the operator's why
        self.assertIn("https://example.com/post-2", body)
        self.assertNotIn("Surfaced by", body)
        lines = note.read_text(encoding="utf-8").split("\n")
        boxes = [l for l in lines if l.strip() in (fb._BOX_OPEN, fb._BOX_DONE)]
        self.assertEqual(boxes, [fb._BOX_OPEN, fb._BOX_DONE, fb._BOX_OPEN])  # item 2 only

    def test_keep_never_captures_twice(self):
        note = self._write_note()
        calls = []

        def fake_capture(vault, body, **kw):
            calls.append(kw)
            return mock.Mock(success=True, path=Path("/x/card.md"), error=None)

        first = fb.keep_item(self.vault, note, 1, "why", capture_fn=fake_capture)
        second = fb.keep_item(self.vault, note, 1, "why", capture_fn=fake_capture)
        self.assertEqual(first.code, fb.EXIT_OK)
        self.assertEqual(second.code, fb.EXIT_OK)
        self.assertIn("already kept", second.message)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["type_hint"], "reference")
        self.assertEqual(calls[0]["source_url"], "https://example.com/post-1")
        self.assertEqual(calls[0]["why"], "why")

    def test_a_refused_capture_leaves_the_box_unticked(self):
        note = self._write_note(1)
        out = fb.keep_item(self.vault, note, 1, "why",
                           capture_fn=lambda *a, **k: mock.Mock(success=False, path=None, error="vault busy"))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn(fb._BOX_OPEN, note.read_text(encoding="utf-8"))
        self.assertNotIn(fb._BOX_DONE, note.read_text(encoding="utf-8"))

    def test_keep_needs_a_why_and_a_real_item(self):
        note = self._write_note(1)
        self.assertEqual(fb.keep_item(self.vault, note, 1, "  ").code, fb.EXIT_USAGE)
        self.assertEqual(fb.keep_item(self.vault, note, 7, "why").code, fb.EXIT_USAGE)

    def test_a_note_resolves_by_its_date(self):
        note = self._write_note(1)
        self.assertEqual(fb.resolve_note(self.vault, "2026-10-04"), note)
        self.assertIsNone(fb.resolve_note(self.vault, "2026-01-01"))


class RaisingRunner(FakeRunner):
    """Answers the engine as `FakeRunner` does and makes `claude` fail the way a
    real subprocess can: raise, or return an exit code and stderr with no stream."""

    def __init__(self, *, exc=None, raw=None, **kw):
        super().__init__("", **kw)
        self.exc, self.raw = exc, raw

    def __call__(self, argv, input_text, cwd, timeout):
        if argv and argv[0] == "claude":
            self.calls.append({"argv": list(argv), "input": input_text, "cwd": cwd})
            if self.exc is not None:
                raise self.exc
            return self.raw
        return super().__call__(argv, input_text, cwd, timeout)


class FlakyMailer:
    """A mailer that answers from a script, then repeats its last answer."""

    def __init__(self, *results):
        self.results, self.sent = list(results), []

    def __call__(self, subject, body):
        self.sent.append({"subject": subject, "body": body})
        i = min(len(self.sent), len(self.results)) - 1
        return dict(self.results[i])


_FAIL = {"sent": False, "configured": True, "relay_reply": None, "skipped": "the relay refused it"}
_OK = {"sent": True, "configured": True, "relay_reply": "relay-id", "skipped": None}


class ToolSurfaceTests(Base):
    """The model must have two tools. The first live version relied on a deny list,
    and a headless run showed nineteen tools, `Read` and `SendMessage` among them:
    `--settings` adds to the operator's allow rules instead of replacing them."""

    def test_the_run_is_given_an_exclusive_tool_list(self):
        r = FakeRunner(stream([_item(1)]))
        self.run_brief(r)
        self.assertIn("--tools=WebSearch,WebFetch", r.claude[0]["argv"])

    def test_the_deny_list_names_what_the_operators_own_settings_allow(self):
        deny = fb.claude_settings()["permissions"]["deny"]
        for tool in ("Read", "Glob", "Grep", "Bash", "Write", "Edit", "Artifact", "SendMessage"):
            self.assertIn(tool, deny)

    def test_a_run_whose_init_event_lists_a_wider_surface_is_refused(self):
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 2),),
                              available=("WebSearch", "WebFetch", "Read", "Glob")))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_AUDIT)
        self.assertIn("Read", out.message)
        self.assertIn("Glob", out.message)
        self.assertFalse(self.note().exists())
        self.assertEqual(out.record["total_cost_usd"], 1.08, "the refused run still reports its cost")

    def test_tool_search_in_the_init_list_is_tolerated(self):
        r = FakeRunner(stream([_item(1)], available=("WebSearch", "WebFetch", "ToolSearch")))
        self.assertEqual(self.run_brief(r).code, fb.EXIT_OK)

    def test_a_refused_attempt_is_recorded_and_is_not_a_violation(self):
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 2), ("Read", 1)), denials=("Read",)))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(out.record["refused"], ["Read"])

    def test_a_call_that_errored_for_want_of_the_tool_is_a_refusal_too(self):
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 2), ("Read", 1)), refused_ids=("Read0",)))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(out.record["refused"], ["Read"])

    def test_one_refusal_does_not_excuse_a_second_call_of_the_same_tool(self):
        # Two Bash calls; the first errored, the second has no error result: it ran.
        r = FakeRunner(stream([_item(1)], tools=(("WebSearch", 1), ("Bash", 2)), refused_ids=("Bash0",)))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_AUDIT)
        self.assertIn("Bash", out.message)

    def test_a_run_that_ended_with_no_result_and_called_an_outside_tool_is_refused(self):
        r = FakeRunner(stream(tools=(("Bash", 1),), no_result=True))
        out = self.run_brief(r)
        self.assertEqual(out.code, fb.EXIT_AUDIT)
        self.assertTrue(out.record["cost_unknown"])


class RunnerFailureTests(Base):
    """`claude -p` is where the money is spent. Whatever goes wrong around it, the
    run hands the runner a last line with a cost, and says when it cannot name one."""

    def test_a_timeout_is_a_failed_run_with_a_cost_that_is_not_known(self):
        partial = stream(tools=(("WebSearch", 2),), no_result=True).encode()
        out = self.run_brief(RaisingRunner(exc=subprocess.TimeoutExpired(["claude"], 900, output=partial)))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn("timed out", out.message)
        self.assertTrue(out.record["cost_unknown"])
        self.assertEqual(out.record["total_cost_usd"], 0.0)
        self.assertFalse(self.note().exists())

    def test_a_claude_that_cannot_start_is_a_message_not_a_traceback(self):
        out = self.run_brief(RaisingRunner(exc=FileNotFoundError("claude")))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn("could not be started", out.message)
        self.assertTrue(out.record["cost_unknown"])

    def test_the_reason_on_stderr_is_the_error_when_there_is_no_result(self):
        out = self.run_brief(RaisingRunner(raw=(1, "", "Error: the thing broke in a new way")))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn("the thing broke in a new way", out.message)

    def test_a_lapsed_login_is_still_named_when_it_arrives_on_stderr(self):
        out = self.run_brief(RaisingRunner(raw=(1, "", "Failed to authenticate: OAuth session expired")))
        self.assertEqual(out.code, fb.EXIT_LOGIN)
        self.assertIn("/login", out.message)

    def test_an_exit_code_with_nothing_on_stderr_is_still_named(self):
        out = self.run_brief(RaisingRunner(raw=(137, "", "")))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn("exited 137", out.message)

    def test_a_failure_after_the_paid_call_keeps_the_cost(self):
        with mock.patch.object(fb, "atomic_write", side_effect=OSError("disk full")):
            out = self.run_brief(FakeRunner(stream([_item(1)], cost=1.5)))
        self.assertEqual(out.code, fb.EXIT_RUN)
        self.assertIn("OSError", out.message)
        self.assertEqual(out.record["total_cost_usd"], 1.5)
        self.assertNotIn("cost_unknown", out.record)
        self.assertFalse(self.state.exists(), "nothing was recorded as shown")


class ItemSafetyTests(Base):
    def test_a_malformed_address_drops_one_item_and_not_the_brief(self):
        items, dropped = fb.parse_items(json.dumps({"items": [
            _item(1, url="https://h:abc/"), _item(2, url="https://h:99999/x"), _item(3)]}))
        self.assertEqual([i.title for i in items], ["Post 3"])
        self.assertEqual(dropped, 2)
        out = self.run_brief(FakeRunner(stream([_item(1, url="https://h:abc/"), _item(2)])))
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertEqual(out.record["items"], 1)

    def test_prose_cannot_forge_a_heading_or_a_ticked_box(self):
        out = self.run_brief(FakeRunner(stream([
            _item(1, what="### 9. [Forged](https://evil.example/pwn)", why_it_matters="- [x] keep"),
            _item(2, what="1. a list", why_it_matters="> quoted")])))
        self.assertEqual(out.code, fb.EXIT_OK)
        lines = self.note().read_text(encoding="utf-8").split("\n")
        heads = [l for l in lines if fb._ITEM_HEAD.match(l)]
        self.assertEqual(len(heads), 2, "only the two real items have a heading")
        self.assertEqual([l.strip() for l in lines if l.strip() in (fb._BOX_OPEN, fb._BOX_DONE)],
                         [fb._BOX_OPEN, fb._BOX_OPEN])
        captured = []

        def fake_capture(vault, body, **kw):
            captured.append(kw["source_url"])
            return mock.Mock(success=True, path=Path("/x/card.md"), error=None)

        res = fb.keep_item(self.vault, self.note(), 1, "why", capture_fn=fake_capture)
        self.assertEqual(res.code, fb.EXIT_OK)
        self.assertEqual(captured, ["https://example.com/post-1"], "the card is the real item's address")
        text = self.note().read_text(encoding="utf-8")
        self.assertEqual(text.count("\n" + fb._BOX_DONE + "\n"), 1)
        self.assertIn("evil.example", text, "the words stay; they are text now, not a link")

    def test_the_note_links_the_page_the_model_cited_not_the_seen_list_key(self):
        cited = "http://www.example.com/docs/page/?id=1234&utm_source=x#install"
        self.run_brief(FakeRunner(stream([_item(1, url=cited)])))
        self.assertIn(f"[Post 1]({cited})", self.note().read_text(encoding="utf-8"))
        self.assertEqual(list(fb.load_seen(self.state, today=_TODAY)), ["https://example.com/docs/page?id=1234"])
        calls = []
        fb.keep_item(self.vault, self.note(), 1, "why",
                     capture_fn=lambda v, b, **kw: calls.append(kw) or mock.Mock(success=True, path=Path("/x"), error=None))
        self.assertEqual(calls[0]["source_url"], cited)

    def test_a_link_cannot_close_early_or_carry_a_credential(self):
        self.assertEqual(fb._safe_link("https://example.com/a(b)c"), "https://example.com/a%28b%29c")
        self.assertEqual(fb._safe_link("https://user:pw@example.com/a"), "https://example.com/a")
        self.assertEqual(fb._safe_link("https://example.com/a b"), "")
        self.assertEqual(fb._safe_link("javascript:alert(1)"), "")

    def test_a_hash_route_is_a_different_page_and_a_plain_anchor_is_not(self):
        self.assertNotEqual(fb.canonical_url("https://app.example.com/#/post/42"),
                            fb.canonical_url("https://app.example.com/#/post/43"))
        self.assertEqual(fb.canonical_url("https://x.example/page#section"), "https://x.example/page")

    def test_one_host_written_three_ways_is_one_address(self):
        self.assertEqual(fb.canonical_url("https://example.com./a"), "https://example.com/a")
        self.assertEqual(fb.canonical_url("https://bücher.example/a"), "https://xn--bcher-kva.example/a")
        self.assertEqual(fb.canonical_url("http://[2001:db8::1]:8080/x"), "https://[2001:db8::1]:8080/x")

    def test_an_address_with_a_port_that_is_not_one_has_no_address(self):
        self.assertEqual(fb.canonical_url("https://h:abc/"), "")
        self.assertEqual(fb.canonical_url("https://h:99999/"), "")


class TornLineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_record_after_a_torn_seen_line_is_not_swallowed_by_it(self):
        path = self.dir / "seen.jsonl"
        path.write_text('{"url": "https://a.example/", "shown": "2026-10-01"}\n{"url": "https://b.exam', encoding="utf-8")
        fb.record_seen(path, ["https://c.example/"], today=date(2026, 10, 2))
        seen = fb.load_seen(path, today=date(2026, 10, 4))
        self.assertIn("https://a.example/", seen)
        self.assertIn("https://c.example/", seen)

    def test_a_record_after_a_torn_mailed_line_is_not_swallowed_by_it(self):
        path = self.dir / "mailed.jsonl"
        path.write_text('{"note": "2026-09-27-field-brief.md", "mailed": "2026-09-27"}\n{"note": "2026-10-0', encoding="utf-8")
        fb.record_mailed(path, "2026-10-04-field-brief.md", date(2026, 10, 4), "id")
        self.assertTrue(fb.already_mailed(path, "2026-10-04-field-brief.md"))
        self.assertTrue(fb.already_mailed(path, "2026-09-27-field-brief.md"))


class MailRobustnessTests(Base):
    def _note_on(self, day, size=400, text=None):
        note = fb.note_path(self.vault, day)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(text if text is not None else
                        "---\ntitle: x\nkind: brief\n---\n\n### 1. [A](https://example.com/a)\n\n"
                        + "word " * (size // 5) + "\n\n- [ ] keep\n", encoding="utf-8")
        return note

    def test_the_footer_names_the_command_not_the_machine(self):
        self.run_brief(FakeRunner(stream([_item(1)])))
        body = self.mailer.sent[0]["body"]
        self.assertIn('/memory field-brief keep 2026-10-04 <item number> --why "<why>"', body)
        for leak in (str(self.vault), str(_REPO), "/Users", "The note:"):
            self.assertNotIn(leak, body)

    def test_a_refused_send_is_retried_after_each_pause_and_then_succeeds(self):
        note = self._note_on(_TODAY)
        mailer = FlakyMailer(_FAIL, _FAIL, _OK)
        pauses = []
        with mock.patch.object(fb.time, "sleep", side_effect=pauses.append):
            rec = fb.deliver_mail(note, _TODAY, enabled=True, mailer=mailer, mailed_state=self.mailed,
                                  delays=(1.5, 4.0))
        self.assertTrue(rec["sent"])
        self.assertEqual(len(mailer.sent), 3)
        self.assertEqual(pauses, [1.5, 4.0])
        self.assertTrue(fb.already_mailed(self.mailed, note.name))

    def test_a_send_that_keeps_failing_is_reported_after_its_last_try(self):
        note = self._note_on(_TODAY)
        mailer = FlakyMailer(_FAIL)
        rec = fb.deliver_mail(note, _TODAY, enabled=True, mailer=mailer, mailed_state=self.mailed,
                              delays=(0.0, 0.0))
        self.assertEqual((rec["sent"], rec["failed"], len(mailer.sent)), (False, True, 3))
        self.assertFalse(fb.already_mailed(self.mailed, note.name))

    def test_an_unconfigured_mail_path_is_not_retried(self):
        note = self._note_on(_TODAY)
        mailer = FlakyMailer({"sent": False, "configured": False, "relay_reply": None, "skipped": "no mail path"})
        rec = fb.deliver_mail(note, _TODAY, enabled=True, mailer=mailer, mailed_state=self.mailed,
                              delays=(0.0, 0.0))
        self.assertEqual(len(mailer.sent), 1)
        self.assertFalse(rec.get("failed"))

    def test_a_mailer_that_raises_is_a_failed_send_and_its_text_goes_nowhere(self):
        note = self._note_on(_TODAY)

        def boom(subject, body):
            raise RuntimeError("smtp://relay:s3cr3t-pass@localhost:587 refused")

        rec = fb.deliver_mail(note, _TODAY, enabled=True, mailer=boom, mailed_state=self.mailed,
                              delays=(0.0,))
        self.assertTrue(rec["failed"])
        self.assertNotIn("s3cr3t-pass", json.dumps(rec))
        self.assertIn("RuntimeError", rec["skipped"])

    def test_a_note_that_cannot_be_read_is_a_failed_send_not_a_crash(self):
        note = self._note_on(_TODAY)
        note.write_bytes(b"\xff\xfe not utf-8 " * 30)
        rec = fb.deliver_mail(note, _TODAY, enabled=True, mailer=FlakyMailer(_OK), mailed_state=self.mailed)
        self.assertTrue(rec["failed"])

    def test_the_next_weekly_run_mails_the_last_weeks_brief_that_never_went_out(self):
        self._note_on(date(2026, 9, 27))
        out = self.run_brief(FakeRunner(stream([_item(1)])))
        self.assertEqual([m["subject"] for m in self.mailer.sent],
                         ["Field brief — week of 2026-09-27", "Field brief — week of 2026-10-04"])
        self.assertEqual(out.record["mail_pending"][0]["note"], "2026-09-27-field-brief.md")
        self.assertTrue(fb.already_mailed(self.mailed, "2026-09-27-field-brief.md"))

    def test_the_catch_up_leaves_alone_what_was_sent_declined_stale_or_a_stub(self):
        sent = self._note_on(date(2026, 10, 1))
        fb.record_mailed(self.mailed, sent.name, date(2026, 10, 1), "id")
        declined = self._note_on(date(2026, 9, 30))
        fb.deliver_mail(declined, date(2026, 9, 30), enabled=False, mailer=self.mailer, mailed_state=self.mailed)
        self._note_on(date(2026, 9, 1))                      # 33 days old
        self._note_on(date(2026, 9, 29), text="---\n---\n")  # a stub a sync left
        self._note_on(date(2026, 10, 5))                     # tomorrow's: not this run's to send
        recs = fb.mail_pending(self.vault, _TODAY, enabled=True, mailer=self.mailer, mailed_state=self.mailed)
        self.assertEqual(recs, [])
        self.assertEqual(self.mailer.sent, [])

    def test_a_no_mail_run_is_not_mailed_by_the_next_weeks_catch_up(self):
        self.run_brief(FakeRunner(stream([_item(1)])), mail=False)
        self.run_brief(FakeRunner(stream([_item(2)])), today=date(2026, 10, 11))
        self.assertEqual([m["subject"] for m in self.mailer.sent], ["Field brief — week of 2026-10-11"])

    def test_a_no_mail_run_can_still_be_mailed_by_a_plain_rerun_that_day(self):
        self.run_brief(FakeRunner(stream([_item(1)])), mail=False)
        r = FakeRunner(stream([_item(2)]))
        out = self.run_brief(r)
        self.assertEqual(r.calls, [])
        self.assertTrue(out.record["mail"]["sent"])
        self.assertEqual(len(self.mailer.sent), 1)

    def test_a_stub_where_the_note_should_be_is_not_the_weeks_brief(self):
        note = self._note_on(_TODAY, text="")
        r = FakeRunner(stream([_item(1)]))
        out = self.run_brief(r)
        self.assertEqual(len(r.claude), 1, "the week's brief was bought")
        self.assertEqual(out.code, fb.EXIT_OK)
        self.assertGreater(note.stat().st_size, 200)


class CommandLineTests(Base):
    def _main(self, argv, runner):
        out, err = io.StringIO(), io.StringIO()
        root = self.state.parent.parent          # the test's own temp directory
        (root / "wiki" / "designs").mkdir(parents=True, exist_ok=True)
        with mock.patch.dict("os.environ", {"AGENTM_STATE_DIR": str(root)}), \
                mock.patch.object(fb, "_REPO", root), \
                redirect_stdout(out), redirect_stderr(err):
            code = fb.main(["--vault-path", str(self.vault)] + argv, runner=runner, mailer=self.mailer)
        return code, out.getvalue(), err.getvalue()

    def test_the_last_stdout_line_is_the_cost_report_the_runner_reads(self):
        code, out, _ = self._main([], FakeRunner(stream([_item(1)], cost=1.5)))
        self.assertEqual(code, fb.EXIT_OK)
        last = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(last["total_cost_usd"], 1.5)
        self.assertEqual(last["job"], "field-brief")
        # and the cycle's own reader agrees
        sys.path.insert(0, str(_REPO))
        from scripts.runner import cycle
        self.assertEqual(cycle._parse_reported_cost(out), 1.5)

    def test_a_crash_still_leaves_a_last_line_that_says_the_cost_is_not_known(self):
        with mock.patch.object(fb, "run_brief", side_effect=RuntimeError("boom")):
            code, out, err = self._main([], FakeRunner(stream([_item(1)])))
        self.assertEqual(code, fb.EXIT_RUN)
        last = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((last["total_cost_usd"], last["cost_unknown"]), (0.0, True))
        self.assertIn("RuntimeError", err)

    def test_mail_pending_sends_the_unsent_brief_and_runs_no_model(self):
        note = fb.note_path(self.vault, _TODAY)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\nkind: brief\n---\n\n### 1. [A](https://example.com/a)\n" + "word " * 60
                        + "\n\n- [ ] keep\n", encoding="utf-8")
        r = FakeRunner(stream([_item(1)]))
        with mock.patch.object(fb, "date", wraps=date) as d:
            d.today.return_value = _TODAY
            code, out, _ = self._main(["--mail-pending"], r)
        self.assertEqual(code, fb.EXIT_OK)
        self.assertEqual(r.calls, [], "no model and no engine")
        self.assertEqual(len(self.mailer.sent), 1)
        last = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((last["mode"], last["total_cost_usd"]), ("mail-pending", 0.0))
        self.assertEqual(last["mail_pending"][0]["note"], "2026-10-04-field-brief.md")

    def test_mail_pending_exits_nonzero_when_the_send_still_fails(self):
        note = fb.note_path(self.vault, _TODAY)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\nkind: brief\n---\n" + "word " * 60, encoding="utf-8")
        self.mailer.result = dict(_FAIL)
        with mock.patch.object(fb, "date", wraps=date) as d:
            d.today.return_value = _TODAY
            code, _, _ = self._main(["--mail-pending"], FakeRunner(""))
        self.assertEqual(code, fb.EXIT_MAIL)

    def test_no_vault_is_a_usage_error(self):
        err = io.StringIO()
        with mock.patch.dict("os.environ", {"MEMORY_ROOT": "", "MEMORY_VAULT_PATH": ""}), redirect_stderr(err):
            self.assertEqual(fb.main([]), fb.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
