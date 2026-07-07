#!/usr/bin/env python3
"""Tests for lifecycle.py — V6-1 per-note lifecycle state (PLAN-wave-e-v6-index task 3).

Covers:
  - lifecycle_tier_for() / is_decay_exempt(): explicit tag, kind proxy
    (error-history), path proxy (architecture-decisions), default volatile.
  - compute_decay_score(): exempt entries always fresh regardless of access
    pattern; volatile entries decay over elapsed days.
  - record_recall_access(): only a genuine recall access resets the volatile
    clock — a raw file touch (simulating a lint walk / index rebuild) must
    NOT reset it; exempt entries are untouched no-ops.
  - recall.query()'s returned payload carries lifecycle_tier + decay_score
    (the "queryable in the recall payload" verification this task names).

Fixture vaults are fully synthetic (tmp dirs), no real vault paths touched.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lifecycle import (  # noqa: E402
    DECAY_HALF_LIFE_DAYS,
    LIFECYCLE_SIDECAR_NAME,
    compute_decay_score,
    is_decay_exempt,
    lifecycle_tier_for,
    record_recall_access,
)
import save  # noqa: E402


def _fm(**overrides) -> dict:
    base = {"kind": "convention", "created": "2026-01-01"}
    base.update(overrides)
    return base


class TestTierClassification(unittest.TestCase):

    def test_explicit_durable_tag(self):
        fm = _fm(lifecycle_tier="durable")
        self.assertTrue(is_decay_exempt(fm, "personal/preferences/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "personal/preferences/some-note.md"), "durable")

    def test_explicit_volatile_tag(self):
        fm = _fm(lifecycle_tier="volatile")
        self.assertFalse(is_decay_exempt(fm, "personal/preferences/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "personal/preferences/some-note.md"), "volatile")

    def test_default_absent_field_is_volatile(self):
        fm = _fm()
        self.assertFalse(is_decay_exempt(fm, "personal/insight/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "personal/insight/some-note.md"), "volatile")

    def test_error_history_kind_is_decay_exempt(self):
        # error-history proxy: kind == failure-incident (gate #2, FABLE R1).
        fm = _fm(kind="failure-incident")
        self.assertTrue(is_decay_exempt(fm, "personal/diagnostics/some-incident.md"))
        self.assertEqual(lifecycle_tier_for(fm, "personal/diagnostics/some-incident.md"), "durable")

    def test_architecture_decisions_path_is_decay_exempt(self):
        # architecture-decisions proxy: a decisions/ path segment (gate #2, FABLE R1).
        fm = _fm()  # no explicit tag, no special kind — path alone must exempt it.
        self.assertTrue(is_decay_exempt(fm, "projects/agentm/decisions/some-call.md"))
        self.assertEqual(lifecycle_tier_for(fm, "projects/agentm/decisions/some-call.md"), "durable")

    def test_decisions_path_exemption_is_directory_segment_not_substring(self):
        # A path merely containing the substring "decisions" without a real
        # directory segment must NOT be exempt (avoid over-matching).
        fm = _fm()
        self.assertFalse(is_decay_exempt(fm, "personal/my-decisions-log.md"))


class TestDecayScore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_decay_exempt_kind_never_decays_regardless_of_elapsed_time(self):
        # Red-test (task 3 verification, bullet 2): error-history never
        # decays no matter how much time passes with no access.
        fm = _fm(kind="failure-incident", created="2020-01-01")
        score_soon = compute_decay_score(
            self.vault, "incident-a", fm, "personal/diag/incident-a.md", now="2020-01-02"
        )
        score_far = compute_decay_score(
            self.vault, "incident-a", fm, "personal/diag/incident-a.md", now="2030-01-01"
        )
        self.assertEqual(score_soon, 1.0)
        self.assertEqual(score_far, 1.0)

    def test_decay_exempt_path_never_decays_regardless_of_elapsed_time(self):
        # Red-test (task 3 verification, bullet 2): architecture-decisions
        # never decay no matter how much time passes with no access.
        fm = _fm(created="2020-01-01")
        rel = "projects/agentm/decisions/some-call.md"
        score_far = compute_decay_score(self.vault, "some-call", fm, rel, now="2035-01-01")
        self.assertEqual(score_far, 1.0)

    def test_volatile_entry_decays_from_created_when_never_accessed(self):
        fm = _fm(created="2026-01-01")
        rel = "personal/insight/some-note.md"
        # Exactly one half-life elapsed with no recorded access.
        now = "2026-01-01"
        import datetime
        later = (
            datetime.date.fromisoformat(now) + datetime.timedelta(days=DECAY_HALF_LIFE_DAYS)
        ).isoformat()
        score = compute_decay_score(self.vault, "some-note", fm, rel, now=later)
        self.assertAlmostEqual(score, 0.5, places=6)

    def test_volatile_entry_no_history_defaults_fresh(self):
        fm = {"kind": "insight"}  # no created, no sidecar entry.
        rel = "personal/insight/no-dates.md"
        score = compute_decay_score(self.vault, "no-dates", fm, rel, now="2026-06-01")
        self.assertEqual(score, 1.0)


class TestAccessDrivenReset(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_genuine_recall_access_resets_volatile_clock(self):
        fm = _fm(created="2020-01-01")
        rel = "personal/insight/some-note.md"
        # Long-decayed before any access.
        stale_score = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-01-01")
        self.assertLess(stale_score, 0.01)

        record_recall_access(self.vault, "some-note", fm, rel, today="2026-06-01")
        fresh_score = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-06-01")
        self.assertEqual(fresh_score, 1.0)

    def test_decay_exempt_entry_access_is_a_no_op(self):
        # Durable tiers ignore access entirely (FABLE R1, adopted-bounded) —
        # recording an access on a decay-exempt entry must not write the
        # sidecar at all.
        fm = _fm(kind="failure-incident", created="2020-01-01")
        rel = "personal/diag/incident-a.md"
        record_recall_access(self.vault, "incident-a", fm, rel, today="2026-06-01")
        sidecar = self.vault / LIFECYCLE_SIDECAR_NAME
        self.assertFalse(sidecar.exists())

    def test_non_recall_file_touch_does_not_reset_the_clock(self):
        # Red-test (task 3 verification, bullet 3): a raw file touch that
        # does NOT go through record_recall_access() — simulating a lint
        # walk or an index rebuild reading the file's content directly —
        # must never reset the decay clock. Only calling the function does.
        fm = _fm(created="2020-01-01")
        rel = "personal/insight/some-note.md"

        # Simulate a lint walk: read the file's content directly (the exact
        # thing vault_lint.build_model() does) without calling into lifecycle.py.
        note_path = self.vault / rel
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("---\nkind: convention\n---\n\nbody\n", encoding="utf-8")
        _ = note_path.read_text(encoding="utf-8")  # the "lint walk" touch

        score_after_raw_touch = compute_decay_score(
            self.vault, "some-note", fm, rel, now="2026-06-01"
        )
        # Still decayed exactly as if nothing had touched the file at all —
        # no sidecar entry was ever created by the raw read.
        sidecar = self.vault / LIFECYCLE_SIDECAR_NAME
        self.assertFalse(sidecar.exists())
        self.assertLess(score_after_raw_touch, 0.01)


class TestRecallPayloadIntegration(unittest.TestCase):
    """V6-20 eval slice: the lifecycle field is populated and queryable in
    the recall payload — recall.query()'s returned dicts carry
    lifecycle_tier + decay_score for every result."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "personal" / "insight").mkdir(parents=True)
        (self.vault / "projects" / "agentm" / "decisions").mkdir(parents=True)
        (self.vault / "personal" / "insight" / "widget-notes.md").write_text(
            "---\nkind: insight\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            "tags: [widget]\ngroup: personal\nslug: widget-notes\nalways_load: false\n---\n\n"
            "Notes about the widget subsystem and its quirks.\n",
            encoding="utf-8",
        )
        (self.vault / "projects" / "agentm" / "decisions" / "widget-call.md").write_text(
            "---\nkind: convention\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            "tags: [widget]\ngroup: personal\nslug: widget-call\nalways_load: false\n---\n\n"
            "Decided the widget subsystem uses approach B, not approach A.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_query_results_carry_lifecycle_fields(self):
        import recall

        results = recall.query(
            vault=self.vault,
            query_text="widget subsystem",
            k=5,
            dedup_paths=set(),
            mode="stub",
        )
        self.assertTrue(results, "expected at least one recall hit for the seeded fixture")
        for r in results:
            self.assertIn("lifecycle_tier", r)
            self.assertIn("decay_score", r)
            self.assertIn(r["lifecycle_tier"], ("durable", "volatile"))

        by_path = {r["path"]: r for r in results}
        decisions_result = by_path.get("projects/agentm/decisions/widget-call.md")
        if decisions_result is not None:
            self.assertEqual(decisions_result["lifecycle_tier"], "durable")
            self.assertEqual(decisions_result["decay_score"], 1.0)


class TestSaveEntryCLISurface(unittest.TestCase):
    """The --lifecycle-tier flag on /memory save (the CLI surface save.py
    exposes for this field, matching --supersedes/--fingerprint's pattern)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_flag_parses(self):
        args = save._parse_args([
            "convention", "a-durable-note",
            "--lifecycle-tier", "durable",
        ])
        self.assertEqual(args.lifecycle_tier, "durable")

    def test_cli_flag_omitted_defaults_to_none(self):
        args = save._parse_args(["convention", "a-note"])
        self.assertIsNone(args.lifecycle_tier)

    def test_cli_flag_rejects_invalid_choice(self):
        with self.assertRaises(SystemExit):
            save._parse_args([
                "convention", "a-note", "--lifecycle-tier", "immortal",
            ])

    def test_save_entry_writes_lifecycle_tier_frontmatter(self):
        path = save.save_entry(
            self.vault, "convention", "a-durable-note", "Body text.\n",
            lifecycle_tier="durable",
        )
        content = path.read_text(encoding="utf-8")
        self.assertIn("lifecycle_tier: durable", content)


if __name__ == "__main__":
    unittest.main()
