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

import lifecycle  # noqa: E402
from lifecycle import (  # noqa: E402
    DECAY_FLOOR,
    LIFECYCLE_SIDECAR_NAME,
    compute_decay_score,
    decay_bands,
    is_decay_exempt,
    lifecycle_tier_for,
    record_recall_access,
)
import save  # noqa: E402


class _FakeRules:
    """The three accessors `lifecycle` asks the contract for. Patched in rather
    than shelling out to the daemon, so a curve test asserts the curve and not
    whether a binary happened to be on the machine."""

    def __init__(self, thresholds):
        self._thresholds = thresholds

    def thresholds(self):
        return dict(self._thresholds)

    def contract_exempt_spaces(self):
        return []


def _with_contract(test, thresholds):
    """Pin the curve to a contract for the duration of one test."""
    original = lifecycle.storage_rules.rules
    lifecycle.storage_rules.rules = lambda: _FakeRules(thresholds)
    test.addCleanup(lambda: setattr(lifecycle.storage_rules, "rules", original))


# What the shipped contract names. The bands used to be constants in two
# languages; they are one curve now, and this is the file that says so.
_SHIPPED = {
    "decay_full_days": 180,
    "decay_half_days": 365,
    "decay_eighth_days": 1095,
    "decay_floor_days": 1825,
    "decay_floor_weight": 0.0625,
}


def _fm(**overrides) -> dict:
    base = {"kind": "convention", "created": "2026-01-01"}
    base.update(overrides)
    return base


class TestTierClassification(unittest.TestCase):

    def test_explicit_durable_tag(self):
        fm = _fm(lifecycle_tier="durable")
        self.assertTrue(is_decay_exempt(fm, "memory/preferences/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "memory/preferences/some-note.md"), "durable")

    def test_explicit_volatile_tag(self):
        fm = _fm(lifecycle_tier="volatile")
        self.assertFalse(is_decay_exempt(fm, "memory/preferences/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "memory/preferences/some-note.md"), "volatile")

    def test_default_absent_field_is_volatile(self):
        fm = _fm()
        self.assertFalse(is_decay_exempt(fm, "memory/insight/some-note.md"))
        self.assertEqual(lifecycle_tier_for(fm, "memory/insight/some-note.md"), "volatile")

    def test_error_history_kind_is_decay_exempt(self):
        # error-history proxy: kind == failure-incident (gate #2, FABLE R1).
        fm = _fm(kind="failure-incident")
        self.assertTrue(is_decay_exempt(fm, "memory/diagnostics/some-incident.md"))
        self.assertEqual(lifecycle_tier_for(fm, "memory/diagnostics/some-incident.md"), "durable")

    def test_architecture_decisions_path_is_decay_exempt(self):
        # architecture-decisions proxy: a decisions/ path segment (gate #2, FABLE R1).
        fm = _fm()  # no explicit tag, no special kind — path alone must exempt it.
        self.assertTrue(is_decay_exempt(fm, "desk/projects/agentm/decisions/some-call.md"))
        self.assertEqual(lifecycle_tier_for(fm, "desk/projects/agentm/decisions/some-call.md"), "durable")

    def test_decisions_path_exemption_is_directory_segment_not_substring(self):
        # A path merely containing the substring "decisions" without a real
        # directory segment must NOT be exempt (avoid over-matching).
        fm = _fm()
        self.assertFalse(is_decay_exempt(fm, "memory/my-decisions-log.md"))


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
            self.vault, "incident-a", fm, "memory/diag/incident-a.md", now="2020-01-02"
        )
        score_far = compute_decay_score(
            self.vault, "incident-a", fm, "memory/diag/incident-a.md", now="2030-01-01"
        )
        self.assertEqual(score_soon, 1.0)
        self.assertEqual(score_far, 1.0)

    def test_decay_exempt_path_never_decays_regardless_of_elapsed_time(self):
        # Red-test (task 3 verification, bullet 2): architecture-decisions
        # never decay no matter how much time passes with no access.
        fm = _fm(created="2020-01-01")
        rel = "desk/projects/agentm/decisions/some-call.md"
        score_far = compute_decay_score(self.vault, "some-call", fm, rel, now="2035-01-01")
        self.assertEqual(score_far, 1.0)

    def test_volatile_entry_decays_from_created_when_never_accessed(self):
        _with_contract(self, _SHIPPED)
        fm = _fm(created="2026-01-01")
        rel = "memory/insight/some-note.md"
        # One day past the first band, with no recorded access.
        import datetime
        later = (
            datetime.date.fromisoformat("2026-01-01") + datetime.timedelta(days=181)
        ).isoformat()
        score = compute_decay_score(self.vault, "some-note", fm, rel, now=later)
        self.assertEqual(score, 0.5)

    def test_volatile_entry_no_history_defaults_fresh(self):
        fm = {"kind": "insight"}  # no created, no sidecar entry.
        rel = "memory/insight/no-dates.md"
        score = compute_decay_score(self.vault, "no-dates", fm, rel, now="2026-06-01")
        self.assertEqual(score, 1.0)

    def test_updated_preferred_over_created_when_no_access_recorded(self):
        # Task 6 fix: an entry substantively edited today is fresh
        # regardless of how old its original `created` date is — falling
        # back to `created` when `updated` is more recent would penalize a
        # frequently-maintained reference doc for staleness it doesn't
        # have. Caught by this task's own real-vault eval: it silently
        # demoted an accurate, same-day-edited hit out of the top-5.
        fm = _fm(created="2020-01-01", updated="2026-06-01")
        rel = "memory/insight/maintained-doc.md"
        # "now" equals `updated`, not `created` -- should be fully fresh.
        score = compute_decay_score(self.vault, "maintained-doc", fm, rel, now="2026-06-01")
        self.assertEqual(score, 1.0)

    def test_falls_back_to_created_when_updated_absent(self):
        _with_contract(self, _SHIPPED)
        fm = _fm(created="2026-01-01")  # no `updated` key at all.
        rel = "memory/insight/some-note.md"
        import datetime
        later = (
            datetime.date.fromisoformat("2026-01-01") + datetime.timedelta(days=181)
        ).isoformat()
        score = compute_decay_score(self.vault, "some-note", fm, rel, now=later)
        self.assertEqual(score, 0.5)


class TestTheDecayCurve(unittest.TestCase):
    """The one curve, read from the filing contract.

    This class was written for a stepped curve that sat in shadow mode beside a
    live thirty-day exponential, comparing the two. The axis-per-space landing
    promoted the stepped curve, retired the exponential and deleted the
    comparison, so the assertions are rewritten to the shipped curve and the
    band edges move with it: the constants said 182 and the contract says 180.
    A class that compared two curves has no subject once there is one.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _score_at(self, days_elapsed: int) -> float:
        fm = _fm(created="2026-01-01")
        rel = "memory/insight/some-note.md"
        import datetime
        later = (
            datetime.date.fromisoformat("2026-01-01") + datetime.timedelta(days=days_elapsed)
        ).isoformat()
        return compute_decay_score(self.vault, "some-note", fm, rel, now=later)

    def test_full_strength_to_the_contracts_first_band(self):
        _with_contract(self, _SHIPPED)
        self.assertEqual(self._score_at(0), 1.0)
        self.assertEqual(self._score_at(179), 1.0)
        self.assertEqual(self._score_at(180), 1.0)

    def test_the_day_the_constant_and_the_contract_disagreed(self):
        # The Go bands said 182 and the contract says 180. Reading the contract
        # is what makes this day answer 0.5 in both arms instead of one each.
        _with_contract(self, _SHIPPED)
        self.assertEqual(self._score_at(181), 0.5)

    def test_half_strength_to_one_year(self):
        _with_contract(self, _SHIPPED)
        self.assertEqual(self._score_at(365), 0.5)

    def test_an_eighth_from_one_to_three_years(self):
        _with_contract(self, _SHIPPED)
        self.assertEqual(self._score_at(366), 0.125)
        self.assertEqual(self._score_at(1095), 0.125)

    def test_a_sixteenth_from_three_to_five_years_and_beyond(self):
        _with_contract(self, _SHIPPED)
        self.assertEqual(self._score_at(1096), 0.0625)
        self.assertEqual(self._score_at(1825), 0.0625)
        self.assertEqual(self._score_at(10_000), 0.0625)  # floor holds past 5y

    def test_the_contracts_own_numbers_move_the_bands(self):
        # Not a mirror of the shipped values: a different contract, and the
        # curve has to follow it.
        _with_contract(self, {
            "decay_full_days": 10,
            "decay_half_days": 20,
            "decay_eighth_days": 30,
            "decay_floor_days": 40,
            "decay_floor_weight": 0.5,
        })
        self.assertEqual(self._score_at(10), 1.0)
        self.assertEqual(self._score_at(11), 0.5)
        self.assertEqual(self._score_at(21), 0.125)
        self.assertEqual(self._score_at(31), 0.5)
        self.assertEqual(self._score_at(9999), 0.5)

    def test_an_incoherent_contract_falls_back_wholesale(self):
        # Half from the contract and half from a constant is the drift that
        # reading the contract exists to end, and it would be invisible.
        for bad in (
            {},
            {"decay_full_days": 180},
            dict(_SHIPPED, decay_half_days=100),
            dict(_SHIPPED, decay_floor_days="not a number"),
        ):
            with self.subTest(contract=bad):
                _with_contract(self, bad)
                self.assertEqual(decay_bands()[0], (182.0, 1.0))

    def test_an_unreadable_contract_falls_back_to_the_packaged_curve(self):
        def boom():
            raise RuntimeError("no daemon on this machine")

        original = lifecycle.storage_rules.rules
        lifecycle.storage_rules.rules = boom
        self.addCleanup(lambda: setattr(lifecycle.storage_rules, "rules", original))
        self.assertEqual(self._score_at(182), 1.0)
        self.assertEqual(self._score_at(183), 0.5)

    def test_decay_exempt_entry_is_always_full_strength(self):
        _with_contract(self, _SHIPPED)
        fm = _fm(kind="failure-incident", created="2020-01-01")
        rel = "memory/diag/incident-a.md"
        score = compute_decay_score(self.vault, "incident-a", fm, rel, now="2035-01-01")
        self.assertEqual(score, 1.0)

    def test_no_anchor_defaults_fresh(self):
        _with_contract(self, _SHIPPED)
        fm = {"kind": "insight"}  # no created, no sidecar entry.
        rel = "memory/insight/no-dates.md"
        score = compute_decay_score(self.vault, "no-dates", fm, rel, now="2026-06-01")
        self.assertEqual(score, 1.0)

    def test_genuine_recall_access_resets_the_clock(self):
        _with_contract(self, _SHIPPED)
        fm = _fm(created="2020-01-01")
        rel = "memory/insight/some-note.md"
        stale = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-01-01")
        self.assertEqual(stale, 0.0625)

        record_recall_access(self.vault, "some-note", fm, rel, today="2026-06-01")
        fresh = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-06-01")
        self.assertEqual(fresh, 1.0)

    def test_scoring_never_mutates_the_sidecar(self):
        # Reading a score is not an access. Only a genuine recall resets the
        # clock, and this is the assertion that keeps a ranking pass from
        # quietly becoming one.
        _with_contract(self, _SHIPPED)
        fm = _fm(created="2020-01-01")
        rel = "memory/insight/some-note.md"
        compute_decay_score(self.vault, "some-note", fm, rel, now="2026-01-01")
        sidecar = self.vault / LIFECYCLE_SIDECAR_NAME
        self.assertFalse(sidecar.exists())


class TestAccessDrivenReset(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_genuine_recall_access_resets_volatile_clock(self):
        fm = _fm(created="2020-01-01")
        rel = "memory/insight/some-note.md"
        # Long-decayed before any access. The floor, not a number under it:
        # the one curve bottoms out at `decay_floor_weight` by design, where
        # the exponential this replaced would have reached 1e-22.
        stale_score = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-01-01")
        self.assertEqual(stale_score, DECAY_FLOOR)

        record_recall_access(self.vault, "some-note", fm, rel, today="2026-06-01")
        fresh_score = compute_decay_score(self.vault, "some-note", fm, rel, now="2026-06-01")
        self.assertEqual(fresh_score, 1.0)

    def test_decay_exempt_entry_access_is_a_no_op(self):
        # Durable tiers ignore access entirely (FABLE R1, adopted-bounded) —
        # recording an access on a decay-exempt entry must not write the
        # sidecar at all.
        fm = _fm(kind="failure-incident", created="2020-01-01")
        rel = "memory/diag/incident-a.md"
        record_recall_access(self.vault, "incident-a", fm, rel, today="2026-06-01")
        sidecar = self.vault / LIFECYCLE_SIDECAR_NAME
        self.assertFalse(sidecar.exists())

    def test_non_recall_file_touch_does_not_reset_the_clock(self):
        # Red-test (task 3 verification, bullet 3): a raw file touch that
        # does NOT go through record_recall_access() — simulating a lint
        # walk or an index rebuild reading the file's content directly —
        # must never reset the decay clock. Only calling the function does.
        fm = _fm(created="2020-01-01")
        rel = "memory/insight/some-note.md"

        # Simulate a lint walk: read the file's content directly (the exact
        # thing vault_lint.build_model() does) without calling into lifecycle.py.
        note_path = self.vault / rel
        note_path.parent.mkdir(parents=True, exist_ok=True)
        # write_bytes (LF-only), not write_text — real "---\n"-delimited
        # frontmatter must survive byte-for-byte on every OS (write_text
        # translates "\n" to the OS native newline on Windows).
        note_path.write_bytes(b"---\nkind: convention\n---\n\nbody\n")
        _ = note_path.read_text(encoding="utf-8")  # the "lint walk" touch

        score_after_raw_touch = compute_decay_score(
            self.vault, "some-note", fm, rel, now="2026-06-01"
        )
        # Still decayed exactly as if nothing had touched the file at all —
        # no sidecar entry was ever created by the raw read.
        sidecar = self.vault / LIFECYCLE_SIDECAR_NAME
        self.assertFalse(sidecar.exists())
        # At the floor, which is what "still decayed, never reset" reads as on
        # the one curve — a reset would read 1.0.
        self.assertEqual(score_after_raw_touch, DECAY_FLOOR)


class TestRecallPayloadIntegration(unittest.TestCase):
    """V6-20 eval slice: the lifecycle field is populated and queryable in
    the recall payload — recall.query()'s returned dicts carry
    lifecycle_tier + decay_score for every result."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "memory" / "insight").mkdir(parents=True)
        (self.vault / "desk/projects" / "agentm" / "decisions").mkdir(parents=True)
        # write_bytes (LF-only), not write_text — real "---\n"-delimited
        # frontmatter must survive byte-for-byte on every OS.
        (self.vault / "memory" / "insight" / "widget-notes.md").write_bytes((
            "---\nkind: insight\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            "tags: [widget]\ngroup: memory\nslug: widget-notes\nalways_load: false\n---\n\n"
            "Notes about the widget subsystem and its quirks.\n"
        ).encode("utf-8"))
        (self.vault / "desk/projects" / "agentm" / "decisions" / "widget-call.md").write_bytes((
            "---\nkind: convention\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            "tags: [widget]\ngroup: memory\nslug: widget-call\nalways_load: false\n---\n\n"
            "Decided the widget subsystem uses approach B, not approach A.\n"
        ).encode("utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_query_results_carry_lifecycle_fields(self):
        import recall

        results = recall.query(
            vault=self.vault,
            query_text="widget subsystem",
            k=5,
            dedup_paths=set(),
        )
        self.assertTrue(results, "expected at least one recall hit for the seeded fixture")
        for r in results:
            self.assertIn("lifecycle_tier", r)
            self.assertIn("decay_score", r)
            self.assertIn(r["lifecycle_tier"], ("durable", "volatile"))

        by_path = {r["path"]: r for r in results}
        decisions_result = by_path.get("desk/projects/agentm/decisions/widget-call.md")
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


# Every test here gets its own engine state directory, where `.lifecycle.json`
# has lived since the memory-root trims (agentm-vault plan 05). On a hand run
# one test's recorded access reset the decay clock the next test measured,
# which failed seven tests, and the accesses landed in the machine's sidecar,
# which the daemon's decay scorer reads.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
