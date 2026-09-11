#!/usr/bin/env python3
"""The morning note (agentm-vault plan 04, task 6).

One note a night: what ran, what needs you, the corpus in one line, and spend,
each section left out when it has nothing to say. These tests build a night's
worth of records — the enrichment run record, the binary's report, the Python
cycle's report and findings, the lifecycle journal, the runner's markers — in
a scratch directory, and hold the note to what they say. The seams are tested
through the real readers: the session brief shows the note's first section,
and the email carries the note.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE / "health", _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import corpus_scorecard  # noqa: E402
import dream  # noqa: E402
import morning_note as mn  # noqa: E402
import session_brief  # noqa: E402
import session_email  # noqa: E402
from runner import manifest  # noqa: E402

# 05:30 local on the morning of 2026-09-12; the night opened at 02:00.
NOW = datetime(2026, 9, 12, 5, 30).timestamp()
TONIGHT = datetime(2026, 9, 12, 3, 10).timestamp()
THREE_DAYS_AGO = datetime(2026, 9, 9, 3, 10).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat()


def _run(at: float, **over) -> dict:
    run = {
        "at": _iso(at), "model": "opus", "pass_version": "enrich/2", "considered": 130,
        "enriched": 118, "skipped": 12, "failed": 0, "notes_sent": 118, "model_calls": 236,
        "tokens": 812334, "total_cost_usd": 4.21,
        "usage": {"strong": {"input_tokens": 700000, "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 12334, "output_tokens": 100000,
                             "total_cost_usd": 4.21, "calls": 236}},
        "token_lines": {"strong": 1000000, "cheap": 2000000}, "call_guard": 250,
        "verdicts": {"filed_active": 97, "below_floor": 21, "sank": 2,
                     "sank_notes": ["memory/semantic/old-a.md", "memory/semantic/old-b.md"]},
    }
    run.update(over)
    return run


def _report() -> dict:
    return {
        "run_id": "tonight-apply-pass", "mode": "apply", "outcome": "applied",
        "decision": {"due": True, "reason": "due"},
        "plan": {"demoted": [{"rel": "memory/semantic/quiet.md", "days": 400}], "revived": [],
                 "archive_candidates": [{"rel": "memory/semantic/ancient.md", "days": 1900}],
                 "skipped_by_cap": 0, "considered": 700},
        "copies": {"families": [{"canonical": "a"}], "deferred": 0},
        "refile": {"moves": [], "unflags": [], "blocked": []},
        "promote": {"promotions": [{"rel": "memory/semantic/candidate-x.md"}], "existing": []},
        "calendar": {"written": [], "refreshed": 10},
        "mocs": {"pages": [{"rel": "memory/mocs/fix.md", "changed": True},
                           {"rel": "memory/mocs/idea.md", "changed": False}]},
        "dates": {"glossed": [], "aging": 216},
    }


def _card(vault: Path, slug: str, *, status="active", enriched_at=None) -> None:
    d = vault / "memory" / "semantic"
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {slug.replace('-', ' ')}", "type: preference", f"status: {status}",
          f"slug: {slug}", "captured: 2026-09-04T10:00:00Z"]
    if status == "unfiled":
        fm.append("filing_confidence: low")
    if enriched_at:
        fm.append(f"enriched_at: {enriched_at}")
    (d / f"{slug}.md").write_text("---\n" + "\n".join(fm) + "\n---\n\nA body.\n", encoding="utf-8")


def _ask_fine(args):
    if args[0] == "status":
        return {"health": {"queue": {"unfiled": 42, "oldest_age": "7d19h"}}}
    return {"current": 12, "eligible": 181}


def _ask_down(_args):
    raise corpus_scorecard.DaemonUnavailable("agentmd is not on PATH")


class _Night(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="morning-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.vault = self.root / "Agent"
        (self.vault / "memory" / "semantic").mkdir(parents=True)
        self.engine = self.root / "state"
        (self.engine / "dreaming").mkdir(parents=True)
        self.runner = self.root / "runner"
        self.runner.mkdir()
        self.rollup = self.root / "rollup.db"

    # ── the night's records ──
    def runs(self, *runs):
        (self.engine / "enrich-runs.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")

    def binary(self, at=TONIGHT, report=None):
        p = self.engine / "dreaming" / "last-report.json"
        p.write_text(json.dumps(report or _report()), encoding="utf-8")
        os.utime(p, (at, at))

    def python(self, at=TONIGHT, **over):
        cycle = {"run_id": "r", "at": at, "storage_rules_ok": True, "entries": 190,
                 "lint": {"orphan_count": 4, "contradiction_count": 0, "repairable_count": 1},
                 "possible_twins": 1, "same_key": 0, "proposed_facets": 1}
        cycle.update(over)
        (self.engine / "dreaming" / "python-cycle.json").write_text(json.dumps(cycle), encoding="utf-8")

    def findings(self, twins=1, facets=1):
        (self.engine / "dreaming" / "review-proposals.json").write_text(json.dumps({
            "run_id": "r", "at": TONIGHT,
            "twins": [{"a": f"memory/semantic/t{i}-a.md", "b": f"memory/semantic/t{i}-b.md",
                       "similarity": 0.95} for i in range(twins)],
            "same_key": [],
            "facets": [{"label": "garden", "days": 3} for _ in range(facets)],
        }), encoding="utf-8")

    def marker(self, job, at=TONIGHT):
        (self.runner / f"{job}.json").write_text(json.dumps(
            {"status": "done", "last_run": at, "last_real_run": at, "last_cost_usd": 0.0}), encoding="utf-8")

    def last_cycle(self, *outcomes):
        (self.runner / "last-cycle.json").write_text(json.dumps({"outcomes": list(outcomes)}), encoding="utf-8")

    def journal(self, *entries):
        (self.engine / "lifecycle-journal.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def sessions(self, cost, events, at=NOW - 3600):
        conn = sqlite3.connect(self.rollup)
        conn.execute("CREATE TABLE by_window (window_start TEXT, cost_usd REAL, event_count INTEGER)")
        conn.execute("INSERT INTO by_window VALUES (?, ?, ?)",
                     (datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), cost, events))
        conn.commit()
        conn.close()

    def full_night(self):
        older = {"strong": {"input_tokens": 400000, "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 50000, "output_tokens": 50000,
                            "total_cost_usd": 2.0, "calls": 100}}
        self.runs(_run(THREE_DAYS_AGO, tokens=500000, total_cost_usd=2.0, usage=older), _run(TONIGHT))
        self.binary()
        self.python()
        self.findings()
        for job in ("dreaming", "dream", "corpus-scorecard"):
            self.marker(job)
        self.journal({"ts": "2026-09-11T03:20:00+00:00", "rel": "memory/semantic/quiet.md",
                      "from": "active", "to": "dormant", "actor": "agentmdream"})
        _card(self.vault, "judged-once", status="unfiled", enriched_at="2026-09-12T03:12:00Z")
        _card(self.vault, "never-judged", status="unfiled")
        health = self.vault / "diagnostics" / "health"
        health.mkdir(parents=True)
        (health / "2026-09-12-health-scorecard.md").write_text("# scorecard\n", encoding="utf-8")
        os.utime(health / "2026-09-12-health-scorecard.md", (TONIGHT, TONIGHT))
        self.sessions(1.5, 7)

    def build(self, ask=_ask_fine):
        dated, stable, head = mn.build(self.vault, now=NOW, engine_dir=self.engine,
                                       runner_dir=self.runner, rollup=self.rollup, ask=ask)
        return dated.read_text(encoding="utf-8"), dated, stable, head


class TheNote(_Night):
    def test_a_night_writes_one_note_and_its_mirror(self):
        self.full_night()
        text, dated, stable, _ = self.build()
        self.assertEqual(dated, self.vault / "diagnostics" / "morning" / "2026-09-12.md")
        self.assertEqual(stable.read_text(encoding="utf-8"), text)
        self.assertEqual(sorted(p.name for p in dated.parent.iterdir()),
                         ["2026-09-12.md", "latest_morning_note.md"])
        for section in ("## What ran", "## What needs you", "## The corpus", "## Spend"):
            self.assertIn(section, text)

    def test_the_frontmatter_parses_and_carries_the_headline(self):
        self.full_night()
        text, _, _, head = self.build()
        import yaml
        fm = yaml.safe_load(text.split("\n---\n", 1)[0][4:])
        self.assertEqual(fm["kind"], "report")
        self.assertEqual(str(fm["date"]), "2026-09-12")
        self.assertEqual(fm["headline"], head)
        self.assertEqual(head, "enrichment judged 118 (97 active, 2 sank) · the binary applied · "
                               "1 possible twin(s) · 5 list(s) need you")

    def test_what_ran(self):
        self.full_night()
        text, *_ = self.build()
        self.assertIn("- **Enrichment** — 118 judged · 97 filed active · 21 below the floor · 2 sank · "
                      "236 calls · 800,000 tokens against the line · opus.", text)
        self.assertIn("- **The dreaming binary** — apply pass, outcome applied; gate: due.", text)
        self.assertIn("| lifecycle | sank 1, revived 0, archive candidates 1, held by cap 0 |", text)
        self.assertIn("| copies | 1 families collapsed, 0 deferred |", text)
        self.assertIn("| promote | 1 new candidates, 0 already carded |", text)
        self.assertIn("| mocs | 1 regenerated of 2 pages |", text)
        self.assertIn("- **The Python cycle** — 1 possible twin(s) · 0 shared key(s) · 1 proposed "
                      "facet(s) · lint 4 orphan(s), 0 contradiction(s), 1 mis-cased link(s) it would repair.", text)
        # Every step ran, so nothing is listed as skipped.
        self.assertNotIn("Did not run", text)

    def test_what_needs_you(self):
        self.full_night()
        text, *_ = self.build()
        self.assertIn("- **Unfiled below the floor** (1): [[judged-once]]", text)
        self.assertNotIn("[[never-judged]]", text)  # awaiting the batch, not you
        self.assertIn("- **Possible twins** (1): [[t0-a]] and [[t0-b]] (95%)", text)
        self.assertIn("- **Proposed facets** (1): `garden` on 3 days", text)
        self.assertIn("- **Archive candidates** (1): [[ancient]] (1,900 days silent)", text)
        self.assertIn("- **Sank this week** (1): [[quiet]]", text)
        self.assertIn("- The full lists: [[needs-review]].", text)

    def test_a_long_list_shows_five_and_counts_the_rest(self):
        self.full_night()
        self.findings(twins=7)
        text, *_ = self.build()
        self.assertIn("- **Possible twins** (7): ", text)
        self.assertIn("[[t4-a]] and [[t4-b]] (95%), and 2 more", text)
        self.assertNotIn("[[t5-a]]", text)

    def test_the_corpus_line(self):
        self.full_night()
        text, *_ = self.build()
        self.assertIn("semantic 2 · procedural 0 · episodic 0 · entities 0 · crystallized 0 · mocs 0 — "
                      "42 awaiting a judgment, the oldest 7d19h — coverage 12 of 181 stamped at this pass — "
                      "[[2026-09-12-health-scorecard]]", text)

    def test_spend(self):
        self.full_night()
        text, *_ = self.build()
        # Counted the way the line counts: 700,000 in + 100,000 out, the
        # 12,334 cached tokens re-read aside, and those shown as processed.
        self.assertIn("- Last night: 800,000 tokens against the line (strong 800,000 of 1,000,000) · "
                      "812,334 processed · 236 calls of the 250-call guard · $4.21", text)
        self.assertIn("- Seven days: 1,250,000 tokens against the line · 1,312,334 processed across "
                      "2 run(s) · $6.21", text)
        self.assertIn("- Sessions, the last day: $1.50 across 7 event(s)", text)

    def test_a_run_three_days_ago_is_not_last_night(self):
        self.runs(_run(THREE_DAYS_AGO))
        text, *_ = self.build()
        self.assertNotIn("**Enrichment**", text)
        self.assertNotIn("Last night:", text)
        self.assertIn("- Seven days: 800,000 tokens against the line · 812,334 processed across 1 run(s)",
                      text)

    def test_a_hand_lowered_line_is_not_the_line_the_night_is_held_to(self):
        # A by-hand run may lower its line and guard with flags; the run record
        # carries what it ran under. The note holds the night to the operator's
        # numbers — on 2026-09-11 it printed "of 736,407" and "of the 237-call
        # guard", which read as a fourfold overspend.
        self.runs(_run(TONIGHT, token_lines={"strong": 736407, "cheap": 2000000}, call_guard=237))
        text, *_ = self.build()
        self.assertIn("(strong 800,000 of 1,000,000)", text)
        self.assertIn("of the 250-call guard", text)
        self.assertNotIn("736,407", text)
        self.assertNotIn("237-call", text)

    def test_the_operators_numbers_agree_with_the_enforcing_copy(self):
        import re
        go = (_HERE.parent / "daemon" / "internal" / "enrich" / "usage.go").read_text(encoding="utf-8")
        go += (_HERE.parent / "daemon" / "internal" / "enrich" / "batch.go").read_text(encoding="utf-8")

        def const(name):
            m = re.search(rf"{name}\s+(?:int64\s+|int\s+)?=\s*([\d_]+)", go)
            self.assertIsNotNone(m, f"{name} not found in the Go source")
            return int(m.group(1).replace("_", ""))

        self.assertEqual(mn.OPERATOR_LINES["strong"], const("StrongTokenLine"))
        self.assertEqual(mn.OPERATOR_LINES["cheap"], const("CheapTokenLine"))
        self.assertEqual(mn.CALL_GUARD, const("CallGuard"))

    def test_a_budget_stop_is_named(self):
        self.runs(_run(TONIGHT, stopped_by="the call guard (250 calls)"))
        text, *_ = self.build()
        self.assertIn("· opus. Stopped by the call guard (250 calls).", text)

    def test_a_step_that_did_not_run_says_why(self):
        self.marker("dreaming", at=THREE_DAYS_AGO)
        self.last_cycle({"job": "dreaming", "ran": False, "skipped_reason": "outside-window 02:00-06:00"},
                        {"job": "dream", "ran": False, "dry_run": True})
        text, *_ = self.build()
        self.assertIn("- Did not run last night: enrichment (not registered) · the dreaming binary "
                      "(outside-window 02:00-06:00) · the Python cycle (dry run) · the corpus "
                      "scorecard (not registered).", text)

    def test_a_batch_run_by_hand_in_the_window_ran(self):
        # Task 7's supervised batch: the manifest is not registered, and the
        # run happened anyway. The note reports the run, not the manifest.
        self.runs(_run(TONIGHT))
        text, *_ = self.build()
        self.assertIn("**Enrichment** — 118 judged", text)
        self.assertNotIn("enrichment (not registered)", text)

    def test_a_binary_whose_gate_held(self):
        self.binary(at=THREE_DAYS_AGO)
        self.marker("dreaming")
        text, *_ = self.build()
        self.assertIn("- **The dreaming binary** — ran, and its gate held; the last pass was 3 d ago (applied).",
                      text)
        self.assertNotIn("| lifecycle |", text)
        # Its archive candidates still need you: they stand until acted on.
        self.assertIn("- **Archive candidates** (1)", text)

    def test_halted_filing_is_said(self):
        self.python(storage_rules_ok=False, storage_rules_error="routing: not a mapping")
        text, _, _, head = self.build()
        self.assertIn("- **The Python cycle** — filing is halted: the contract did not parse "
                      "(routing: not a mapping).", text)
        self.assertIn("filing is halted", head)

    def test_empty_sections_are_left_out(self):
        text, *_ = self.build(ask=_ask_down)
        self.assertNotIn("## What needs you", text)
        self.assertNotIn("## Spend", text)
        self.assertIn("the queue not measured (agentmd is not on PATH)", text)
        self.assertIn("coverage not measured (agentmd is not on PATH)", text)

    def test_a_coverage_read_that_cannot_be_right_is_read_again(self):
        # 2026-09-11 13:42: "coverage 0 of 0" straight after the batch, while
        # the ledger held 18 of 183. The second read is the one reported.
        _card(self.vault, "a-card")
        answers = iter([{"eligible": 0, "current": 0}, {"eligible": 183, "current": 18}])

        def ask(args):
            if args[0] == "status":
                return {"health": {"queue": {"unfiled": 15}}}
            return next(answers)

        night = mn.gather(self.vault, now=NOW, engine_dir=self.engine, runner_dir=self.runner,
                          rollup=self.rollup, out_dir=self.vault / mn.DIAGNOSTICS_DIR, ask=ask, pause=0)
        self.assertIn("coverage 18 of 183 stamped at this pass", mn.corpus_line(night)[0])

    def test_a_coverage_zero_that_stays_is_not_printed_as_a_zero(self):
        _card(self.vault, "a-card")

        def ask(args):
            if args[0] == "status":
                return {"health": {"queue": {"unfiled": 15}}}
            return {"eligible": 0, "current": 0}

        night = mn.gather(self.vault, now=NOW, engine_dir=self.engine, runner_dir=self.runner,
                          rollup=self.rollup, out_dir=self.vault / mn.DIAGNOSTICS_DIR, ask=ask, pause=0)
        line = mn.corpus_line(night)[0]
        self.assertNotIn("coverage 0 of 0", line)
        self.assertIn("coverage not measured (the ledger answered 0 eligible twice over a corpus of 1 cards",
                      line)

    def test_an_empty_corpus_may_honestly_read_zero(self):
        # No cards at all: zero eligible is the true answer, read once.
        calls = []

        def ask(args):
            calls.append(args[0])
            if args[0] == "status":
                return {"health": {"queue": {"unfiled": 0}}}
            return {"eligible": 0, "current": 0}

        night = mn.gather(self.vault, now=NOW, engine_dir=self.engine, runner_dir=self.runner,
                          rollup=self.rollup, out_dir=self.vault / mn.DIAGNOSTICS_DIR, ask=ask, pause=0)
        self.assertIn("coverage 0 of 0 stamped at this pass", mn.corpus_line(night)[0])
        self.assertEqual(calls.count("ledger"), 1)

    def test_the_night_opens_at_two(self):
        self.assertEqual(mn.night_start(datetime(2026, 9, 12, 5, 30).timestamp()),
                         datetime(2026, 9, 12, 2, 0).timestamp())
        self.assertEqual(mn.night_start(datetime(2026, 9, 12, 2, 0).timestamp()),
                         datetime(2026, 9, 12, 2, 0).timestamp())
        self.assertEqual(mn.night_start(datetime(2026, 9, 12, 1, 30).timestamp()),
                         datetime(2026, 9, 11, 2, 0).timestamp())

    def test_the_cycle_report_name_agrees_with_the_cycle(self):
        self.assertEqual(mn.CYCLE_REPORT.name, dream.CYCLE_REPORT_NAME)

    def test_the_night_writes_nothing_else(self):
        self.full_night()
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.build()
        after = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(sorted(set(after) - set(before)),
                         ["Agent/diagnostics/morning/2026-09-12.md",
                          "Agent/diagnostics/morning/latest_morning_note.md"])


class TheSeams(_Night):
    """The note is read by two surfaces; each is tested through its real reader."""

    def test_the_session_brief_shows_the_first_section(self):
        self.full_night()
        _, _, _, head = self.build()
        brief = session_brief.build_brief(
            vault=self.vault, now=datetime.fromtimestamp(NOW).astimezone(),
            park_dir=self.root / "park", history_path=self.root / "hist.jsonl",
            runner_cycle_path=self.root / "none.json")
        self.assertTrue(brief["line"].startswith(f"[agentm] Morning — {head} (written "))

    def test_the_email_carries_the_note(self):
        self.full_night()
        text, _, _, head = self.build()
        subject, body = session_email.email_body(self.vault, now=datetime.fromtimestamp(NOW).astimezone())
        self.assertEqual(subject, f"AgentM morning — {head}")
        self.assertEqual(body, text.split("\n---\n", 1)[1].lstrip("\n"))

    def test_the_job_is_the_nights_last_writer(self):
        template = _HERE.parent / "templates" / "jobs" / "morning-note.yaml"
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(template, Path(td) / "morning-note.yaml")
            (job,) = manifest.load_manifests(Path(td))
        self.assertEqual((job.window, job.order, job.tier, job.dry_run),
                         ("02:00-06:00", 5, "T2", False))
        self.assertIn("morning_note.py", job.command)


# Every test here gets its own engine state dir.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
