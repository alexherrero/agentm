#!/usr/bin/env python3
"""The governance lanes (filing v2 part 6, task 2).

A transition edits `lifecycle:` in place and journals who moved it; the
policy sinks the silent to `dormant` and lifts the recalled back, under a
cap; `archived` is unreachable except through a confirm surface — the
proposal the dream cycle stages and the operator confirms, or the
operator's own hand; pinned and superseded never move by policy; the
digest says what quietly sank; the scorecard carries the line.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import corpus_scorecard  # noqa: E402
import dream  # noqa: E402
import dream_confirm  # noqa: E402
import lifecycle  # noqa: E402
import lifecycle_transitions as lt  # noqa: E402
from revert_log import RevertLog  # noqa: E402

TODAY = "2026-09-05"
NOW = TODAY + "T09:00:00+00:00"


def _ago(days: int) -> str:
    return (dt.date.fromisoformat(TODAY) - dt.timedelta(days=days)).isoformat()


class _Rules:
    def __init__(self, dormant=365, archive=1825):
        self._t = {"dormant_after_days": dormant, "archive_after_days": archive}

    def thresholds(self):
        return dict(self._t)

    def lifecycles(self):
        return list(lt.STATES)


class _Vault(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="lifecycle-lanes-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.vault = self.top / "vault"
        (self.vault / "memory" / "semantic").mkdir(parents=True)
        self.state = self.top / "state"
        self._env = {k: os.environ.get(k) for k in ("AGENTM_STATE_DIR",)}
        os.environ["AGENTM_STATE_DIR"] = str(self.state)
        self.addCleanup(self._restore)
        self.rules = _Rules()

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _note(self, name, *, lifecycle=None, created=None, extra="", body="A plain body.\n", cls="semantic"):
        rel = f"memory/{cls}/{name}.md"
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fm = f"---\ntitle: {name}\nkind: reference\nstatus: active\nslug: {name}\n"
        if lifecycle:
            fm += f"lifecycle: {lifecycle}\n"
        if created:
            fm += f"created: {created}\n"
        p.write_text(fm + extra + "---\n\n" + body, encoding="utf-8")
        return rel

    def _journal(self):
        return lt.journal_entries()

    def _state(self, rel):
        return lt.lifecycle_of((self.vault / rel).read_text(encoding="utf-8"))


class TheEdit(unittest.TestCase):
    def test_replaces_the_line_and_dates_it_without_touching_the_body(self):
        text = "---\ntitle: T\nlifecycle: active\ntags: [a]\n---\n\nBody stays.\n"
        new = lt.set_lifecycle_text(text, "dormant", since=TODAY)
        self.assertIn("lifecycle: dormant\nlifecycle_since: 2026-09-05\ntags: [a]\n", new)
        self.assertTrue(new.endswith("---\n\nBody stays.\n"))
        self.assertEqual(lt.lifecycle_of(new), "dormant")

    def test_inserts_the_line_when_the_note_carries_none(self):
        text = "---\ntitle: T\n---\nBody.\n"
        new = lt.set_lifecycle_text(text, "dormant", since=TODAY)
        self.assertEqual(new, "---\ntitle: T\nlifecycle: dormant\nlifecycle_since: 2026-09-05\n---\nBody.\n")
        self.assertEqual(lt.lifecycle_of(text), "active", "no axis reads as the default")

    def test_a_second_move_updates_the_date(self):
        once = lt.set_lifecycle_text("---\nlifecycle: active\n---\n", "dormant", since="2026-01-01")
        twice = lt.set_lifecycle_text(once, "active", since=TODAY)
        self.assertEqual(twice, "---\nlifecycle: active\nlifecycle_since: 2026-09-05\n---\n")

    def test_no_frontmatter_is_left_alone(self):
        self.assertEqual(lt.set_lifecycle_text("just a body\n", "dormant", since=TODAY), "just a body\n")


class OneTransition(_Vault):
    def test_moves_in_place_and_journals_who_did_it(self):
        rel = self._note("a", lifecycle="active")
        e = lt.transition(self.vault, rel, "dormant", actor="policy", reason="silent", now=NOW, run_id="r1", rules=self.rules)
        self.assertTrue(e["changed"])
        self.assertEqual(self._state(rel), "dormant")
        self.assertTrue((self.vault / rel).exists(), "files never move for lifecycle")
        self.assertEqual(sorted(p.name for p in (self.vault / "memory/semantic").iterdir()), ["a.md"])
        j = self._journal()
        self.assertEqual(len(j), 1)
        self.assertEqual({k: j[0][k] for k in ("rel", "from", "to", "actor", "reason", "run_id")},
                         {"rel": rel, "from": "active", "to": "dormant", "actor": "policy", "reason": "silent", "run_id": "r1"})
        self.assertEqual(j[0]["ts"], NOW)

    def test_already_there_is_a_no_op_that_journals_nothing(self):
        rel = self._note("a", lifecycle="dormant")
        e = lt.transition(self.vault, rel, "dormant", actor="policy", now=NOW, rules=self.rules)
        self.assertFalse(e["changed"])
        self.assertEqual(self._journal(), [])

    def test_a_value_the_contract_does_not_name_is_refused(self):
        rel = self._note("a")
        with self.assertRaises(lt.NotOnTheAxis):
            lt.transition(self.vault, rel, "expired", actor="operator", rules=self.rules)
        with self.assertRaises(ValueError):
            lt.transition(self.vault, rel, "dormant", actor="cron", rules=self.rules)

    def test_archived_needs_a_confirm_surface(self):
        rel = self._note("a", lifecycle="dormant")
        with self.assertRaises(lt.ConfirmationRequired):
            lt.transition(self.vault, rel, "archived", actor="policy", now=NOW, rules=self.rules)
        self.assertEqual(self._state(rel), "dormant")
        self.assertEqual(self._journal(), [])
        for actor in lt.CONFIRMED_ACTORS:
            rel2 = self._note(f"b-{actor}", lifecycle="dormant")
            lt.transition(self.vault, rel2, "archived", actor=actor, now=NOW, rules=self.rules)
            self.assertEqual(self._state(rel2), "archived")
        self.assertEqual([e["actor"] for e in self._journal()], list(lt.CONFIRMED_ACTORS))


class ThePolicy(_Vault):
    def test_a_silent_active_memory_sinks_and_a_recent_one_does_not(self):
        old = self._note("old", lifecycle="active", created=_ago(400))
        fresh = self._note("fresh", lifecycle="active", created=_ago(30))
        bare = self._note("bare", created=_ago(400))  # no axis at all: the default, active
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules, run_id="cycle-1")
        self.assertEqual(sorted(rel for rel, _ in r.demoted), sorted([old, bare]))
        self.assertEqual(self._state(old), "dormant")
        self.assertEqual(self._state(bare), "dormant")
        self.assertEqual(self._state(fresh), "active")
        self.assertIn("lifecycle_since: 2026-09-05", (self.vault / old).read_text(encoding="utf-8"))
        j = self._journal()
        self.assertEqual({e["rel"] for e in j}, {old, bare})
        self.assertTrue(all(e["actor"] == "policy" and "dormant_after_days" in e["reason"] and e["run_id"] == "cycle-1" for e in j))

    def test_a_recalled_dormant_memory_is_lifted_back(self):
        rel = self._note("back", lifecycle="dormant", created=_ago(900))
        fm = {"kind": "reference", "slug": "back"}
        lifecycle.record_recall_access(self.vault, "back", fm, rel, today=TODAY)
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules)
        self.assertEqual([rel for rel, _ in r.revived], [rel])
        self.assertEqual(self._state(rel), "active")
        self.assertEqual(self._journal()[0]["to"], "active")

    def test_a_dormant_memory_past_the_archive_line_is_named_never_moved(self):
        rel = self._note("cold", lifecycle="dormant", created=_ago(2000))
        near = self._note("nearly", lifecycle="dormant", created=_ago(1700))  # past 0.9 × 1825 = 1642.5
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules)
        self.assertEqual([x for x, _ in r.archive_candidates], [rel])
        self.assertEqual([x for x, _ in r.previews], [near])
        self.assertEqual(self._state(rel), "dormant", "policy never archives")
        self.assertEqual(self._journal(), [])

    def test_pinned_superseded_and_archived_never_move_by_policy(self):
        pinned = self._note("p", lifecycle="pinned", created=_ago(3000))
        sup = self._note("s", lifecycle="superseded", created=_ago(3000))
        arch = self._note("r", lifecycle="archived", created=_ago(3000))
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules)
        self.assertEqual((r.demoted, r.revived, r.archive_candidates), ([], [], []))
        self.assertEqual([self._state(x) for x in (pinned, sup, arch)], ["pinned", "superseded", "archived"])
        self.assertEqual(self._journal(), [])

    def test_a_decay_exempt_memory_is_skipped(self):
        rel = self._note("durable", lifecycle="active", created=_ago(3000), extra="lifecycle_tier: durable\n")
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules)
        self.assertEqual(r.demoted, [])
        self.assertEqual(self._state(rel), "active")

    def test_the_cap_bounds_one_cycle(self):
        for i in range(3):
            self._note(f"old-{i}", lifecycle="active", created=_ago(500))
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules, cap=1)
        self.assertEqual((len(r.demoted), r.skipped_by_cap), (1, 2))
        self.assertEqual(len(self._journal()), 1)

    def test_report_only_moves_nothing(self):
        rel = self._note("old", lifecycle="active", created=_ago(500))
        r = lt.policy_pass(self.vault, now=NOW, rules=self.rules, apply=False)
        self.assertEqual([x for x, _ in r.demoted], [rel])
        self.assertEqual(self._state(rel), "active")
        self.assertEqual(self._journal(), [])

    def test_thresholds_come_from_the_contract(self):
        self.assertEqual(lt.thresholds(_Rules(dormant=30, archive=90)), (30.0, 90.0))

        class _Old:
            def thresholds(self):
                return {"archive_after_days": 1825}
        self.assertEqual(lt.thresholds(_Old()), (lt.DEFAULT_DORMANT_AFTER_DAYS, 1825.0))


class TheConfirmSurface(_Vault):
    def test_the_dream_stage_proposes_an_in_place_edit_and_applies_nothing(self):
        rel = self._note("cold", lifecycle="dormant", created=_ago(2000))
        near = self._note("nearly", lifecycle="dormant", created=_ago(1700))
        proposals, previews = dream._stage_lifecycle(self.vault, now=TODAY, rules=self.rules)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual((p.stage, p.kind, p.paths), ("lifecycle", "archive", [rel]))
        (path, content), = p.mutations
        self.assertEqual(Path(path), self.vault / rel)
        self.assertEqual(content, lt.archive_proposal_text((self.vault / rel).read_text(encoding="utf-8"), since=TODAY))
        self.assertIn("lifecycle: archived", content)
        self.assertEqual(self._state(rel), "dormant", "a proposal applies nothing")
        self.assertEqual(len(previews), 1)
        self.assertIn(near, previews[0])
        self.assertNotIn("lifecycle", dream_confirm.AUTO_APPLY_STAGES)
        self.assertIn("lifecycle", dream._ANOMALY_WATCHED_STAGES)

    def test_a_confirmed_archive_lands_in_place_and_is_journaled_by_the_confirm_surface(self):
        rel = self._note("cold", lifecycle="dormant", created=_ago(2000))
        proposals, _ = dream._stage_lifecycle(self.vault, now=TODAY, rules=self.rules)
        digest = dream.DreamDigest(run_id="run-archive", corpus_stats=dream._stage_corpus_stats([]), proposals=proposals, insight_candidates=[])
        dream._stage_digest_and_staging(self.vault, digest)
        rl = RevertLog(self.vault, log_root=self.top / "revert", lock_root=self.top / "locks")
        entry_id = dream_confirm.confirm(self.vault, "run-archive", 1, rl, lock_root=self.top / "locks")
        self.assertEqual(self._state(rel), "archived")
        self.assertTrue((self.vault / rel).exists())
        j = self._journal()
        self.assertEqual(len(j), 1)
        self.assertEqual((j[0]["rel"], j[0]["to"], j[0]["actor"], j[0]["run_id"]), (rel, "archived", "dream-confirm", "run-archive"))
        self.assertIn(entry_id, j[0]["reason"])


class TheReading(_Vault):
    def test_summary_counts_states_and_the_weeks_moves(self):
        self._note("p", lifecycle="pinned"); self._note("a1"); self._note("a2", lifecycle="active")
        old = self._note("old", lifecycle="active", created=_ago(400))
        lt.policy_pass(self.vault, now=NOW, rules=self.rules)
        lt.journal_append({"ts": _ago(20) + "T00:00:00+00:00", "rel": "memory/semantic/x.md", "from": "active",
                           "to": "dormant", "actor": "policy", "reason": "old", "run_id": None})
        s = lt.summarize(self.vault, now=TODAY, rules=self.rules)
        self.assertEqual(s["populations"]["pinned"], 1)
        self.assertEqual(s["populations"]["active"], 2)
        self.assertEqual(s["populations"]["dormant"], 1)
        self.assertEqual(s["moves"]["sank"], 1, "the three-week-old move is outside the window")
        self.assertIn("dormant 1", lt.describe(s))
        self.assertIn("sank 1", lt.describe(s))
        self.assertEqual(self._state(old), "dormant")

    def test_the_digest_says_what_quietly_sank(self):
        r = lt.PolicyResult(demoted=[("memory/semantic/old.md", 402.0)], revived=[("memory/semantic/back.md", 3.0)],
                            archive_candidates=[("memory/semantic/cold.md", 2000.0)])
        digest = dream.DreamDigest(run_id="run-x", corpus_stats=dream._stage_corpus_stats([]), proposals=[], insight_candidates=[], lifecycle=r.as_dict())
        text = dream._render_digest(digest)
        self.assertIn("What quietly sank", text)
        self.assertIn("memory/semantic/old.md", text)
        self.assertIn("402 days", text)
        self.assertIn("memory/semantic/back.md", text)
        self.assertIn("1 archive proposal", text)

    def test_the_scorecard_carries_the_line(self):
        self._note("a"); self._note("d", lifecycle="dormant", created=_ago(500))
        reading = corpus_scorecard._lifecycle_reading(self.vault, today=TODAY)
        self.assertEqual(reading.label, "lifecycle")
        self.assertEqual(reading.value, 1)
        self.assertIn("active 1", reading.note)
        self.assertIn("dormant 1", reading.note)


class TheCycle(_Vault):
    def test_the_policy_rides_the_dream_cycle(self):
        old = self._note("old", lifecycle="active", created=_ago(500))
        digest, _batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-cycle", log_root=self.top / "revert", lock_root=self.top / "locks",
            include_inbox_triage=False)
        self.assertEqual(self._state(old), "dormant")
        self.assertIsNotNone(digest.lifecycle)
        self.assertEqual([x for x, _ in digest.lifecycle["demoted"]], [old])
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("What quietly sank", text)
        self.assertEqual(self._journal()[0]["run_id"], "run-cycle")


if __name__ == "__main__":
    unittest.main()
