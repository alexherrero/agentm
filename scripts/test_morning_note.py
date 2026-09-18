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
from unittest import mock

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


def _go_stamp(epoch: float, fraction: str) -> str:
    """An instant the way agentmd's run record writes it. Go's RFC3339Nano
    drops a fraction's trailing zeros, so the fraction runs from one digit to
    nine."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction}Z"


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


def _crystallize_run(at: float, **over) -> dict:
    run = {
        "at": _iso(at), "job": "crystallize", "model": "opus", "tier": "strong",
        "why": "pinned to the strong tier without audit", "sources": 412,
        "clusters": 2, "considered": 2, "model_calls": 2, "tokens": 48000,
        "total_cost_usd": 1.40,
        "usage": {"strong": {"input_tokens": 40000, "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 9000, "output_tokens": 8000,
                             "total_cost_usd": 1.40, "calls": 2}},
        "by_job": {"crystallize": {"input_tokens": 40000,
                                   "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 9000,
                                   "output_tokens": 8000, "cost_usd": 1.40, "calls": 2}},
        "lessons": [{"rel": "memory/crystallized/worktree-guard.md",
                     "subject": "worktree-guard",
                     "title": "A worktree guard refuses what it cannot verify",
                     "why": "Three tasks hit it.",
                     "consolidated_from": ["a", "b", "c"],
                     "stamped": ["memory/semantic/a.md"]}],
    }
    run.update(over)
    return run


def _report() -> dict:
    return {
        "run_id": "tonight-apply-pass", "mode": "apply", "outcome": "applied",
        "decision": {"due": True, "reason": "due"},
        "plan": {"demoted": [{"rel": "memory/semantic/quiet.md", "days": 400}], "revived": [],
                 "archive_candidates": [{"rel": "memory/semantic/ancient.md", "days": 1900}],
                 "archived": [{"rel": "memory/semantic/ancient.md", "days": 1900}],
                 "sinking_within_30_days": [{"rel": "memory/semantic/nearly.md", "days": 355}],
                 "archiving_within_30_days": [{"rel": "memory/semantic/soon.md", "days": 1815}],
                 "skipped_by_cap": 0, "considered": 700},
        "facet": {"rel": "../calendar/2026/2026-09-12-dreaming.md", "acts": 3},
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
        self.vault = self.root / "agent"
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

    def crystallize(self, *runs):
        (self.engine / "crystallize-runs.jsonl").write_text(
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

    def last_cycle(self, *outcomes, at=TONIGHT):
        (self.runner / "last-cycle.json").write_text(
            json.dumps({"at": at, "outcomes": list(outcomes)}), encoding="utf-8")

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


class TheSpendLine(_Night):
    """The design's ask (session 4's second loose end): the spend section gains
    per-job lines for enrichment deep, enrichment light and crystallize, beside
    the nightly and seven-day totals. The weekly phase's cadence re-audit reads
    its own line after three runs, and one total cannot answer what that phase
    costs while the batch spends on the same nights."""

    def by_job(self):
        return {"classify-unfiled": {"input_tokens": 500000,
                                     "cache_creation_input_tokens": 0,
                                     "cache_read_input_tokens": 10000,
                                     "output_tokens": 80000,
                                     "cost_usd": 3.60, "calls": 190},
                "summarize": {"input_tokens": 200000,
                              "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 2334,
                              "output_tokens": 20000,
                              "cost_usd": 0.61, "calls": 46}}

    def test_the_spend_line_names_each_job(self):
        self.full_night()
        self.runs(_run(TONIGHT, by_job=self.by_job()))
        self.crystallize(_crystallize_run(TONIGHT))
        text, *_ = self.build()

        for want in ("enrichment deep", "enrichment light", "crystallize"):
            self.assertIn(want, text, f"the spend line does not name {want}")
        # The numbers are the job's own, not the night's total.
        self.assertIn("580,000 tokens · 190 call(s) · $3.60", text)
        self.assertIn("48,000 tokens · 2 call(s) · $1.40", text)

    def test_the_weekly_phase_gets_its_own_line(self):
        self.full_night()
        self.crystallize(_crystallize_run(TONIGHT))
        text, *_ = self.build()
        self.assertIn("The weekly phase: 1 lesson(s) from 2 recurrence(s)", text)

    def test_the_seven_day_total_carries_the_weekly_phase(self):
        self.full_night()
        self.crystallize(_crystallize_run(TONIGHT))
        text, *_ = self.build()
        # The batch's $4.21 tonight + $2.00 three days ago + the phase's $1.40.
        self.assertIn("$7.61", text)

    def test_a_run_written_before_by_job_existed_still_counts_in_the_totals(self):
        """The per-job lines are a finer reading of the same spend, not a second
        accounting of it. A record written before the meter carried job names
        has no `by_job` block, and must not make the night's total disappear."""
        self.full_night()  # _run() carries no by_job
        text, *_ = self.build()
        self.assertIn("$4.21", text)
        self.assertNotIn("enrichment deep", text)

    def test_every_lesson_is_named_where_you_would_argue_with_it(self):
        """The re-audit trigger for this phase is "any crystallized note you
        would not keep", which cannot be read from a count. A lesson never
        decays and nothing ages it out, so the morning it is written is the
        cheapest morning to disagree with it."""
        self.full_night()
        self.crystallize(_crystallize_run(TONIGHT))
        text, *_ = self.build()
        self.assertIn("Lessons this week", text)
        self.assertIn("[[worktree-guard]]", text)


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
        # Seven, not five: the two forward lists and the day's facet joined
        # what needs the operator's eye when the archive candidates left it.
        self.assertEqual(head, "enrichment judged 118 (97 active, 2 sank) · the binary applied · "
                               "1 possible twin(s) · 7 list(s) need you")

    def test_what_ran(self):
        self.full_night()
        text, *_ = self.build()
        self.assertIn("- **Enrichment** — 118 judged · 97 filed active · 21 below the floor · 2 sank · "
                      "236 calls · 800,000 tokens against the line · opus.", text)
        self.assertNotIn("project records", text)
        self.assertIn("- **The dreaming binary** — apply pass, outcome applied; gate: due.", text)
        self.assertIn("| lifecycle | sank 1, revived 0, archive candidates 1, held by cap 0 |", text)
        self.assertIn("| copies | 1 families collapsed, 0 deferred |", text)
        self.assertIn("| promote | 1 new candidates, 0 already carded |", text)
        self.assertIn("| mocs | 1 regenerated of 2 pages |", text)
        self.assertIn("- **The Python cycle** — 1 possible twin(s) · 0 shared key(s) · 1 proposed "
                      "facet(s) · lint 4 orphan(s), 0 contradiction(s), 1 mis-cased link(s) it would repair.", text)
        # Every step ran, so nothing is listed as skipped.
        self.assertNotIn("Did not run", text)

    def test_a_night_that_merged_project_records_counts_them_beside_the_verdicts(self):
        self.full_night()
        self.runs(_run(TONIGHT, verdicts={"filed_active": 97, "below_floor": 21, "sank": 2, "records": 5}))
        text, *_ = self.build()
        self.assertIn("- **Enrichment** — 118 judged · 97 filed active · 21 below the floor · 2 sank · "
                      "5 project records · 236 calls · ", text)

    def test_what_needs_you(self):
        self.full_night()
        text, *_ = self.build()
        self.assertIn("- **Unfiled below the floor** (1): [[judged-once]]", text)
        self.assertNotIn("[[never-judged]]", text)  # awaiting the batch, not you
        self.assertIn("- **Possible twins** (1): [[t0-a]] and [[t0-b]] (95%)", text)
        self.assertIn("- **Proposed facets** (1): `garden` on 3 days", text)
        self.assertIn("- **Sank this week** (1): [[quiet]]", text)
        # The archive candidates used to be listed here as what the operator had
        # to confirm. The night moves them itself now, so what is worth their eye
        # is what is coming — a threshold can be argued with while it is still
        # thirty days off — and the day's facet, which names every move made.
        self.assertNotIn("Archive candidates", text)
        self.assertIn("- **Sinking within 30 days** (1): [[nearly]] (355 days silent)", text)
        self.assertIn("- **Archiving within 30 days** (1): [[soon]] (1,815 days silent)", text)
        self.assertIn("- **What the night did** (3 act(s)): [[2026-09-12-dreaming]]", text)
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
        self.assertIn("- Last night: 800,000 tokens against the line (strong 800,000 of 2,000,000) · "
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

    def test_a_run_counts_whatever_the_length_of_its_fraction(self):
        # 2026-09-13. agentmd writes `at` as Go's RFC3339Nano, which trims a
        # fraction's trailing zeros, and fromisoformat takes only three digits
        # or six before Python 3.11. The note read 61 judged over two runs and
        # missed a third, with its 4 project records and $3.16.
        merged = _run(TONIGHT + 3600, notes_sent=4, model_calls=4, tokens=40000, total_cost_usd=3.16,
                      verdicts={"records": 4},
                      usage={"strong": {"input_tokens": 30000, "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 0, "output_tokens": 10000}})
        merged["at"] = _go_stamp(TONIGHT + 3600, "80599")
        nano = _run(TONIGHT + 5400, notes_sent=3, model_calls=6, failed=1, tokens=21000, total_cost_usd=0.5,
                    verdicts={"filed_active": 2, "below_floor": 1, "sank": 0},
                    usage={"strong": {"input_tokens": 15000, "cache_creation_input_tokens": 1000,
                                      "cache_read_input_tokens": 0, "output_tokens": 5000}})
        nano["at"] = _go_stamp(TONIGHT + 5400, "805990123")
        self.runs(_run(TONIGHT), merged, nano)
        text, *_, head = self.build()
        self.assertIn("- **Enrichment** — 125 judged · 99 filed active · 22 below the floor · 2 sank · "
                      "4 project records · 246 calls · 861,000 tokens against the line · opus · 1 failed.",
                      text)
        self.assertIn("- Last night: 861,000 tokens against the line (strong 861,000 of 2,000,000) · "
                      "873,334 processed · 246 calls of the 250-call guard · $7.87", text)
        self.assertIn("- Seven days: 861,000 tokens against the line · 873,334 processed across "
                      "3 run(s) · $7.87", text)
        self.assertTrue(head.startswith("enrichment judged 125 (99 active, 2 sank) · "), head)

    def test_a_run_whose_at_will_not_parse_is_still_left_out(self):
        # Only the fraction's length is evened out. A day or an hour that does
        # not exist stays unreadable, even with a fraction that is padded first.
        for stamp in ("not a time", "2026-02-30T10:10:00.80599Z", "2026-09-12T25:10:00.80599Z"):
            with self.subTest(stamp=stamp):
                unreadable = _run(TONIGHT, notes_sent=500, total_cost_usd=99.0)
                unreadable["at"] = stamp
                self.runs(_run(TONIGHT), unreadable)
                text, *_ = self.build()
                self.assertIn("- **Enrichment** — 118 judged · ", text)
                self.assertIn("across 1 run(s) · $4.21", text)

    def test_a_fraction_is_read_to_the_microsecond_whatever_its_length(self):
        # Nine digits are cut to six, not rounded, as fromisoformat itself does
        # from Python 3.11 on.
        def utc(microsecond):
            return datetime(2026, 9, 14, 1, 26, 17, microsecond, tzinfo=timezone.utc).timestamp()

        self.assertEqual(mn._epoch("2026-09-14T01:26:17Z"), utc(0))
        self.assertEqual(mn._epoch("2026-09-14T01:26:17.8Z"), utc(800000))
        self.assertEqual(mn._epoch("2026-09-14T01:26:17.80599Z"), utc(805990))
        self.assertEqual(mn._epoch("2026-09-14T01:26:17.805990999Z"), utc(805990))
        self.assertEqual(mn._epoch("2026-09-13T18:26:17.80599-07:00"), utc(805990))
        self.assertIsNone(mn._epoch("2026-02-30T01:26:17.80599Z"))
        self.assertIsNone(mn._epoch(""))
        self.assertIsNone(mn._epoch(None))

    def test_a_hand_lowered_line_is_not_the_line_the_night_is_held_to(self):
        # A by-hand run may lower its line and guard with flags; the run record
        # carries what it ran under. The note holds the night to the operator's
        # numbers — on 2026-09-11 it printed "of 736,407" and "of the 237-call
        # guard", which read as a fourfold overspend.
        self.runs(_run(TONIGHT, token_lines={"strong": 736407, "cheap": 2000000}, call_guard=237))
        text, *_ = self.build()
        self.assertIn("(strong 800,000 of 2,000,000)", text)
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

    def test_a_growing_refusal_set_is_not_silent(self):
        """A refused card leaves no stamp, so nothing on disk says it was asked
        about. The note is where a set that is quietly growing becomes visible."""
        self.full_night()
        self.runs(_run(THREE_DAYS_AGO), _run(TONIGHT, refused=17, refusals_open=19))
        text, *_, head = self.build()
        self.assertIn("19 card(s) stand refused at this pass, 17 of them skipped "
                      "free rather than judged again.", text)
        self.assertIn("19 refused", head)

    def test_the_first_night_of_refusals_reads_without_a_saving(self):
        self.full_night()
        self.runs(_run(TONIGHT, refused=0, refusals_open=19))
        text, *_ = self.build()
        self.assertIn("19 card(s) stand refused at this pass.", text)
        self.assertNotIn("skipped free", text)

    def test_nothing_standing_says_nothing(self):
        """The silence has to mean 'none', not 'nobody counted' — a run record
        written before the refusal record existed carries neither number."""
        self.full_night()
        text, *_, head = self.build()
        self.assertNotIn("stand refused", text)
        self.assertNotIn("refused", head)

    def test_a_budget_stop_is_named(self):
        self.runs(_run(TONIGHT, stopped_by="the call guard (250 calls)"))
        text, *_ = self.build()
        self.assertIn("· opus. Stopped by the call guard (250 calls).", text)

    def test_a_step_that_did_not_run_says_why(self):
        # The account is from a cycle inside tonight's window, so its word is
        # about tonight.
        self.marker("dreaming", at=THREE_DAYS_AGO)
        self.last_cycle({"job": "dreaming", "ran": False, "skipped_reason": "missed-beyond-lookback"},
                        {"job": "dream", "ran": False, "dry_run": True})
        text, *_ = self.build()
        self.assertIn("- Did not run last night: enrichment (not registered) · the dreaming binary "
                      "(missed-beyond-lookback) · the Python cycle (dry run) · the corpus "
                      "scorecard (not registered).", text)

    def test_a_cycle_from_before_the_window_cannot_say_why(self):
        # 2026-09-13. The note runs inside a cycle, and a cycle writes its
        # account only when it ends, so at 02:26 the note read the account of
        # the cycle that started at 01:52, before the window opened, where
        # every night step was outside the window. It gave that as the reason
        # enrichment did not run. The 02:22 cycle it ran in had held the batch
        # at the budget ceiling, and the 02:56 cycle ran it.
        now = datetime(2026, 9, 13, 2, 26, 12).timestamp()
        for job in ("dreaming", "dream", "corpus-scorecard"):
            self.marker(job, at=datetime(2026, 9, 13, 2, 22, 45).timestamp())
        self.marker("enrich-nightly", at=datetime(2026, 9, 12, 2, 23, 25).timestamp())
        self.runs(_run(datetime(2026, 9, 12, 2, 33).timestamp()))
        self.last_cycle(*({"job": job, "ran": False, "skipped_reason": "outside-window 02:00-06:00"}
                          for job, _label in mn.NIGHT_JOBS),
                        at=datetime(2026, 9, 13, 1, 52, 45).timestamp())
        dated, _, head = mn.build(self.vault, now=now, engine_dir=self.engine, runner_dir=self.runner,
                                  rollup=self.rollup, ask=_ask_fine)
        text = dated.read_text(encoding="utf-8")
        self.assertNotIn("outside-window", text)
        self.assertIn("- Did not run last night: enrichment (no reason on record for the night).", text)
        self.assertTrue(head.startswith("enrichment did not run (no reason on record for the night) · "), head)

    def test_a_cycle_after_the_window_cannot_say_why_either(self):
        # Run by hand at 13:00, the note reads a cycle the window had closed on.
        self.marker("enrich-nightly", at=THREE_DAYS_AGO)
        self.last_cycle({"job": "enrich-nightly", "ran": False, "skipped_reason": "outside-window 02:00-06:00"},
                        at=datetime(2026, 9, 12, 12, 41).timestamp())
        dated, _, _ = mn.build(self.vault, now=datetime(2026, 9, 12, 13, 0).timestamp(), engine_dir=self.engine,
                               runner_dir=self.runner, rollup=self.rollup, ask=_ask_fine)
        text = dated.read_text(encoding="utf-8")
        self.assertIn("- Did not run last night: enrichment (no reason on record for the night)", text)
        self.assertNotIn("outside-window", text)

    def test_a_step_switched_off_says_so_whichever_cycle_saw_it(self):
        # A cycle reads the switch and the watchdog before it looks at the
        # window, so the cycle before the night saw both as well as one inside
        # it would have.
        self.last_cycle({"job": "enrich-nightly", "ran": False, "skipped_reason": "disabled"},
                        {"job": "dreaming", "ran": False, "skipped_reason": "watchdog-stop"},
                        at=datetime(2026, 9, 12, 1, 52).timestamp())
        text, *_ = self.build()
        self.assertIn("- Did not run last night: enrichment (disabled) · the dreaming binary "
                      "(watchdog-stop) · ", text)

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
        # A pass whose gate held did not run tonight, and what it said last time
        # about what is coming still stands.
        self.assertIn("- **Sinking within 30 days** (1)", text)

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
                         ["agent/diagnostics/morning/2026-09-12.md",
                          "agent/diagnostics/morning/latest_morning_note.md"])


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

    def test_the_daemon_is_asked_under_the_runners_memory_root(self):
        # The runner exports the memory root as MEMORY_ROOT and, for one
        # release, as its deprecated alias MEMORY_VAULT_PATH. agentmd reads
        # either as the memory root and derives the vault root from it
        # (2026-09-11), so the report no longer strips the variable before
        # asking: under the old strip a scratch export never reached the
        # daemon at all, and the daemon's answer came from its config alone.
        stub = self.vault / "stub_agentmd.py"
        stub.write_text("import json, os, sys\n"
                        "print(json.dumps({'root': os.environ.get('MEMORY_ROOT'),"
                        " 'alias': os.environ.get('MEMORY_VAULT_PATH')}))\n",
                        encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("MEMORY_ROOT", "MEMORY_VAULT_PATH")}
        old_bin = corpus_scorecard.DAEMON_BIN
        os.environ["MEMORY_ROOT"] = str(self.vault)
        os.environ["MEMORY_VAULT_PATH"] = str(self.vault)
        corpus_scorecard.DAEMON_BIN = sys.executable
        try:
            got = corpus_scorecard._agentmd([str(stub)])
        finally:
            corpus_scorecard.DAEMON_BIN = old_bin
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(got, {"root": str(self.vault), "alias": str(self.vault)})

    def test_the_job_is_the_nights_last_writer(self):
        template = _HERE.parent / "templates" / "jobs" / "morning-note.yaml"
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(template, Path(td) / "morning-note.yaml")
            (job,) = manifest.load_manifests(Path(td))
        self.assertEqual((job.window, job.order, job.tier, job.dry_run),
                         ("02:00-06:00", 5, "T2", False))
        self.assertIn("morning_note.py", job.command)


class TheRunnerDirectoryIsTheRunnersOwn(unittest.TestCase):
    """`default_runner_state` copies `runner.state.default_state_root`,
    because the skill's scripts stand alone once installed. The note reads the
    night from where the runner writes it only while the two agree, so this
    holds them equal with the cache root moved and with none set (2026-09-14).
    """

    def test_with_the_cache_root_moved(self):
        from runner import state as runner_state

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": td}):
            self.assertEqual(mn.default_runner_state(), Path(td) / "agentm" / "runner")
            self.assertEqual(mn.default_runner_state(), runner_state.default_state_root())

    def test_with_no_cache_root_set(self):
        from runner import state as runner_state

        without = {k: v for k, v in os.environ.items() if k != "XDG_CACHE_HOME"}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, without, clear=True), \
                mock.patch.object(Path, "home", return_value=Path(td)):
            self.assertEqual(mn.default_runner_state(), Path(td) / ".cache" / "agentm" / "runner")
            self.assertEqual(mn.default_runner_state(), runner_state.default_state_root())


# Every test here gets its own engine state dir.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
