#!/usr/bin/env python3
"""Unit tests for `dream.py` — the Python half of the night (agentm-vault
plan 04, task 5).

The cycle reads, reports and proposes; it changes no note. What these tests
hold:
  - a full pass over a seeded corpus finds the twins (dedup at 0.92) and the
    shared keys (contradiction triage), and every source note is
    byte-identical afterwards;
  - each finding is a pair a reviewer can act on — both paths, the
    similarity, both titles — and it lands in the needs-review map, the
    review-proposals file and the cycle report the morning note reads;
  - a settled note (superseded, archived) is never matched again;
  - the retired stages leave no trace: no staging, no insights, no
    auto-expired record, no opinion pointers;
  - the CLI still accepts the flags a pre-plan-04 manifest passes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream  # noqa: E402


class _DreamTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        self._env = {k: os.environ.get(k) for k in ("AGENTM_STATE_DIR", "XDG_CACHE_HOME")}
        os.environ["AGENTM_STATE_DIR"] = str(Path(self._tmp.name) / "state")
        os.environ["XDG_CACHE_HOME"] = str(Path(self._tmp.name) / "cache")
        self.addCleanup(self._restore_env)


    def _restore_env(self) -> None:
        # Hand runs share one engine state dir across tests unless each test
        # governs its own (filing-v2 remainders task 6); the battery's runner
        # did this from outside, and a bare `python3 test_dream.py` did not.
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    def _write(self, name: str, content: str) -> Path:
        path = self.vault / name
        path.write_text(content, encoding="utf-8")
        return path

    def _snapshot(self, paths: list) -> dict:
        return {str(p): p.read_bytes() for p in paths}


class FullPassFixtureTests(_DreamTestBase):
    """A seeded corpus with a twin pair, a shared-key pair, a supersession
    chain and one control note, run through the whole cycle once."""

    def setUp(self) -> None:
        super().setUp()
        self.dup_a = self._write(
            "dup-a.md", "---\nslug: dup\nkind: fix\n---\nThe server retries three times on timeout.\n"
        )
        self.dup_b = self._write(
            "dup-b.md", "---\nslug: dup-b\nkind: fix\n---\nThe server retries three times on timeout!\n"
        )
        self.con_a = self._write(
            "con-a.md", "---\nslug: contradiction\nkind: preference\n---\nUse tabs for indentation.\n"
        )
        self.con_b = self._write(
            "con-b.md", "---\nslug: contradiction\nkind: preference\n---\nUse spaces for indentation.\n"
        )
        # The chain compression used to collapse. It retired; the chain must
        # come through untouched and unproposed.
        self.chain_1 = self._write("chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md"))
        self.chain_2 = self._write("chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md"))
        self.chain_3 = self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")
        self.control = self._write("control.md", "---\nkind: workflow\n---\nCompletely unrelated content about cats.\n")
        self.all_paths = [
            self.dup_a, self.dup_b, self.con_a, self.con_b,
            self.chain_1, self.chain_2, self.chain_3, self.control,
        ]
        self.pre_snapshot = self._snapshot(self.all_paths)

    def test_the_twins_and_the_shared_key_are_found(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture")
        kinds = sorted(p.kind for p in digest.proposals)
        self.assertEqual(kinds, ["possible-twin", "same-key"])
        twin = next(p for p in digest.proposals if p.kind == "possible-twin")
        self.assertEqual(twin.paths, ["dup-a.md", "dup-b.md"])
        self.assertGreaterEqual(twin.detail["similarity"], dream.DEDUP_SIMILARITY_THRESHOLD)
        self.assertEqual((twin.detail["a_title"], twin.detail["b_title"]), ("dup a", "dup b"))
        same = next(p for p in digest.proposals if p.kind == "same-key")
        self.assertEqual(same.detail["slug"], "contradiction")
        self.assertEqual(sorted(same.paths), ["con-a.md", "con-b.md"])

    def test_no_note_changes(self) -> None:
        dream.run_dream(self.vault, run_id="run-fixture-2")
        self.assertEqual(self._snapshot(self.all_paths), self.pre_snapshot)

    def test_the_control_and_the_chain_appear_in_no_finding(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture-3")
        touched = {path for prop in digest.proposals for path in prop.paths}
        for untouched in ("control.md", "chain-1.md", "chain-2.md", "chain-3.md"):
            self.assertNotIn(untouched, touched)

    def test_the_digest_lists_every_finding_for_you_to_judge(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture-4")
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("## For you to judge", text)
        for p in digest.proposals:
            self.assertIn(f"- {p.stage} · {p.kind}: {p.summary}", text)
        self.assertEqual(digest.digest_path.parent.name, "run-fixture-4")

    def test_the_findings_reach_the_needs_review_map(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture-5")
        state = dream.engine_state.engine_state_dir() / "dreaming"
        found = json.loads((state / dream.REVIEW_PROPOSALS_NAME).read_text(encoding="utf-8"))
        self.assertEqual(found["run_id"], "run-fixture-5")
        self.assertEqual(len(found["twins"]), 1)
        self.assertEqual(len(found["same_key"]), 1)
        self.assertEqual(found["facets"], [])
        moc = (self.vault / "memory" / "mocs" / "needs-review.md").read_text(encoding="utf-8")
        self.assertIn("## Possible twins (1)", moc)
        self.assertIn("[[dup-a]] and [[dup-b]]", moc)
        self.assertIn("## Shared keys, different bodies (1)", moc)
        self.assertEqual(digest.needs_review["dreaming"], {"twins": 1, "same_key": 1, "facets": 0})

    def test_the_cycle_report_counts_what_ran(self) -> None:
        dream.run_dream(self.vault, run_id="run-fixture-6")
        report = json.loads((dream.engine_state.engine_state_dir() / "dreaming"
                             / dream.CYCLE_REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(report["run_id"], "run-fixture-6")
        self.assertTrue(report["storage_rules_ok"])
        self.assertEqual(report["entries"], 8)
        self.assertEqual((report["possible_twins"], report["same_key"], report["proposed_facets"]),
                         (1, 1, 0))
        self.assertIn("orphan_count", report["lint"])


class DedupThresholdTests(_DreamTestBase):
    def test_below_threshold_is_not_proposed(self) -> None:
        self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog.\n")
        self._write("b.md", "---\nkind: fix\n---\nCompletely different subject matter about spreadsheets.\n")
        digest = dream.run_dream(self.vault, run_id="run-below")
        self.assertEqual([p for p in digest.proposals if p.stage == "dedup"], [])

    def test_above_threshold_is_a_possible_twin_with_nothing_to_apply(self) -> None:
        self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n")
        self._write("b.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n")
        digest = dream.run_dream(self.vault, run_id="run-above")
        twins = [p for p in digest.proposals if p.stage == "dedup"]
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0].kind, "possible-twin")
        # A finding, never a staged edit: the proposal type has no field that
        # could carry one.
        self.assertFalse(hasattr(twins[0], "mutations"))

    def test_the_threshold_is_the_shipped_one(self) -> None:
        self.assertEqual(dream.DEDUP_SIMILARITY_THRESHOLD, 0.92)

    def test_three_copies_are_two_pairs_not_three(self) -> None:
        # Each note joins at most one pair as the second half, so a family of
        # copies reads as the first copy paired with each of the others.
        for name in ("a", "b", "c"):
            self._write(f"{name}.md", "---\nkind: fix\n---\nThe same sentence, copied three times over.\n")
        digest = dream.run_dream(self.vault, run_id="run-family")
        pairs = sorted(tuple(p.paths) for p in digest.proposals if p.kind == "possible-twin")
        self.assertEqual(pairs, [("a.md", "b.md"), ("a.md", "c.md")])


class ContradictionTriageTests(_DreamTestBase):
    def test_a_shared_key_with_different_bodies_is_found(self) -> None:
        self._write("a.md", "---\nslug: x\n---\nOption A.\n")
        self._write("b.md", "---\nslug: x\n---\nOption B.\n")
        digest = dream.run_dream(self.vault, run_id="run-contra")
        contra = [p for p in digest.proposals if p.stage == "contradiction_triage"]
        self.assertEqual(len(contra), 1)
        self.assertEqual(contra[0].kind, "same-key")
        self.assertEqual(contra[0].detail["slug"], "x")

    def test_same_slug_identical_body_is_not_a_contradiction(self) -> None:
        self._write("a.md", "---\nslug: x\n---\nSame content.\n")
        self._write("b.md", "---\nslug: x\n---\nSame content.\n")
        digest = dream.run_dream(self.vault, run_id="run-identical")
        self.assertEqual([p for p in digest.proposals if p.stage == "contradiction_triage"], [])


class OpinionsDirExclusionTests(_DreamTestBase):
    """Accumulate loop, Stages 2-3, locked call 6 (second half): `_opinions/`
    joins `_EXCLUDE_DIRS`. Before this fix the directory sat in the general
    corpus, so a served supplement or a lane entry could be merged, shelved,
    or link-annotated by the wrong stage — changing text the agent reads as
    its own standards."""

    def test_iter_entries_skips_opinions_dir(self) -> None:
        (self.vault / "memory" / "_opinions" / "done").mkdir(parents=True)
        self._write("memory/_opinions/done/lesson.md", "---\nkind: opinion-supplement\n---\nAlways X.\n")
        self._write("memory/_opinions/done.md", "---\nkind: opinion-supplement\n---\nServed.\n")
        self._write("ordinary.md", "---\nkind: workflow\n---\nUnrelated content.\n")
        entries = dream._iter_entries(self.vault)
        rels = {p.relative_to(self.vault) for p in entries}
        self.assertEqual(rels, {Path("ordinary.md")})

    def test_iter_entries_skips_crystallized_lanes_and_served_supplements(self) -> None:
        """Filing-v2 part 3: the lanes fold into memory/crystallized/ and the
        same hazard follows them; a crystallized memory beside them stays in
        the general corpus."""
        (self.vault / "memory" / "crystallized" / "done").mkdir(parents=True)
        self._write("memory/crystallized/done/lesson.md", "---\nkind: opinion-supplement\n---\nAlways X.\n")
        self._write("memory/crystallized/done.md", "---\nkind: opinion-supplement\nstatus: promoted\n---\nServed.\n")
        self._write("memory/crystallized/distilled.md", "---\ntype: workflow\nstatus: active\n---\nA lesson.\n")
        entries = dream._iter_entries(self.vault)
        rels = {p.relative_to(self.vault) for p in entries}
        self.assertEqual(rels, {Path("memory/crystallized/distilled.md")})

    def test_a_near_verbatim_pair_inside_a_retired_lane_is_no_twin(self) -> None:
        # The lanes retired with the opinion supplement. Whatever is left on
        # disk is nobody's corpus, and a standard is never called a twin.
        (self.vault / "memory" / "_opinions" / "done").mkdir(parents=True)
        self._write("memory/_opinions/done/a.md", "---\nkind: opinion-supplement\n---\nAlways run the gates first.\n")
        self._write("memory/_opinions/done/b.md", "---\nkind: opinion-supplement\n---\nAlways run the gates first!\n")
        digest = dream.run_dream(self.vault, run_id="run-opinions-exclusion")
        self.assertEqual(digest.proposals, [])


class RetiredStagesLeaveNoTrace(_DreamTestBase):
    """Plan 04 retired every stage that applied or staged anything. A pass
    over a corpus that would have fed each of them must write none of their
    files."""

    def test_nothing_the_retired_stages_wrote_appears(self) -> None:
        self._write("dup-a.md", "---\nkind: fix\n---\nThe server retries three times on timeout.\n")
        self._write("dup-b.md", "---\nkind: fix\n---\nThe server retries three times on timeout!\n")
        self._write("chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-2.md"))
        self._write("chain-2.md", "---\nkind: fix\n---\nFix v1.\n")
        (self.vault / "memory" / "_opinions" / "good").mkdir(parents=True)
        self._write("memory/_opinions/good/a1.md",
                    "---\nkind: opinion-supplement\nstatus: proposed\nsessions: [p/s1]\n---\nLint first.\n")
        dream.run_dream(self.vault, run_id="run-retired")
        state = dream.engine_state.engine_state_dir()
        for gone in ("dream-staging", "dream-auto-expired-latest.json",
                     "opinion-base-proposals.json", "opinion-supplement-health-latest.json",
                     "crystallize-staging"):
            self.assertFalse((state / gone).exists(), gone)
        run_dir = state / "dream-runs" / "run-retired"
        self.assertEqual(sorted(p.name for p in run_dir.iterdir()), ["digest.md"])
        self.assertFalse((self.vault / "_dream").exists())
        self.assertFalse((self.vault / "memory" / "_opinions" / "good.md").exists())


class HaltedFilingProposesNothing(_DreamTestBase):
    def test_a_broken_contract_halts_the_findings_and_says_why(self) -> None:
        self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n")
        self._write("b.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n")
        err = dream.storage_rules.StorageRulesError("routing: line 3: not a mapping")
        with unittest.mock.patch.object(dream.storage_rules, "load", side_effect=err):
            digest = dream.run_dream(self.vault, run_id="run-halted")
        self.assertEqual(digest.proposals, [])
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("**Filing is halted.**", text)
        self.assertIn("routing: line 3: not a mapping", text)
        report = json.loads((dream.engine_state.engine_state_dir() / "dreaming"
                             / dream.CYCLE_REPORT_NAME).read_text(encoding="utf-8"))
        self.assertFalse(report["storage_rules_ok"])


class EmptyRunTests(_DreamTestBase):
    def test_a_corpus_with_nothing_to_judge_says_so(self) -> None:
        self._write("solo.md", "---\nkind: workflow\n---\nNothing to dedup, no slug, no chain.\n")
        digest = dream.run_dream(self.vault, run_id="run-empty")
        self.assertEqual(digest.proposals, [])
        self.assertIn("Nothing this run.", digest.digest_path.read_text(encoding="utf-8"))


class CliTests(_DreamTestBase):
    def test_main_smoke_run(self) -> None:
        self._write("a.md", "---\nkind: workflow\n---\nJust one file.\n")
        rc = dream.main(["--vault-path", str(self.vault), "--run-id", "cli-run"])
        self.assertEqual(rc, 0)
        self.assertTrue((dream.engine_state.engine_state_dir() / "dream-runs" / "cli-run" / "digest.md").exists())

    def test_main_no_vault_path_errors(self) -> None:
        prev = os.environ.pop("MEMORY_VAULT_PATH", None)
        try:
            rc = dream.main([])
        finally:
            if prev is not None:
                os.environ["MEMORY_VAULT_PATH"] = prev
        self.assertEqual(rc, 1)

    def test_a_pre_plan_04_manifest_still_runs(self) -> None:
        # The live dream.yaml passed `--batch-cap 25`, and a hand run might
        # pass `--no-auto-apply`. Both are accepted and change nothing: there
        # is nothing left to cap or to hold back.
        self._write("a.md", "---\nkind: workflow\n---\nJust one file.\n")
        before = (self.vault / "a.md").read_bytes()
        rc = dream.main(["--vault-path", str(self.vault), "--run-id", "cli-legacy",
                         "--batch-cap", "25", "--no-auto-apply"])
        self.assertEqual(rc, 0)
        self.assertEqual((self.vault / "a.md").read_bytes(), before)


class ConnectivityMeterTests(_DreamTestBase):
    """Task 7 verification: the two counts are computed independently, and
    a note with only a generated (Related-line) link doesn't count toward
    organic connectivity. Pure content parsing -- no sqlite-vec, never
    skips."""

    def _load(self):
        entries = dream._iter_entries(self.vault)
        return dream._load(entries)

    def test_generated_only_note_does_not_count_as_organic(self) -> None:
        # A: organic link in prose. B: ONLY a generated Related-line link.
        # C: no links at all.
        self._write("a.md", "---\nslug: a\n---\nsee [[b]] for the details\n")
        self._write("b.md", "---\nslug: b\n---\nbody\n\n**Related:** [[a]]\n")
        self._write("c.md", "---\nslug: c\n---\nno links here\n")

        meter = dream._connectivity_meter(self._load())

        self.assertEqual(meter["organically_linked_count"], 1)  # only A
        self.assertAlmostEqual(meter["organic_connectivity"], 1 / 3)
        self.assertEqual(meter["generated_link_count"], 1)  # B's Related link

    def test_counts_are_independent_organic_note_with_generated_links_too(self) -> None:
        # A note with BOTH an organic prose link and a generated Related
        # line counts organic once AND contributes its Related links to
        # the generated count -- the two numbers move independently.
        self._write(
            "both.md",
            "---\nslug: both\n---\nbuilds on [[other]]\n\n**Related:** [[x]], [[y]]\n",
        )
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 1)
        self.assertEqual(meter["generated_link_count"], 2)

    def test_fenced_wikilink_counts_toward_neither(self) -> None:
        self._write(
            "fenced.md",
            "---\nslug: fenced\n---\nexample:\n\n```\nsee [[example-link]]\n**Related:** [[fenced-example]]\n```\n",
        )
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 0)
        self.assertEqual(meter["generated_link_count"], 0)

    def test_supersedes_frontmatter_counts_as_organic(self) -> None:
        self._write("super.md", "---\nslug: super\nsupersedes: old-note\n---\nbody\n")
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 1)

    def test_empty_corpus_reports_zero_not_crash(self) -> None:
        meter = dream._connectivity_meter({})
        self.assertEqual(meter["organic_connectivity"], 0.0)
        self.assertEqual(meter["generated_link_count"], 0)

    def test_both_numbers_land_in_the_digest(self) -> None:
        self._write("a.md", "---\nslug: a\n---\nsee [[b]]\n")
        self._write("b.md", "---\nslug: b\n---\nbody\n\n**Related:** [[a]]\n")
        digest = dream.run_dream(self.vault, run_id="run-meter-1")
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Connectivity:", digest_text)
        self.assertIn("organic", digest_text)
        self.assertIn("1 generated link(s) (counted separately)", digest_text)
        self.assertEqual(digest.corpus_stats["organically_linked_count"], 1)
        self.assertEqual(digest.corpus_stats["generated_link_count"], 1)


class BrowseSurfaceCountsTests(_DreamTestBase):
    """Task 8 verification: a fixture cycle exercising all three states
    (a live note, a shelved artifact, an archived memory) produces
    correct counts in both the digest and `corpus_stats` (the dashboard's
    data source). The acceptance test this meter encodes: browsing shows
    live notes, and archived material is still readable on request."""

    def _write(self, name: str, content: str):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_three_states_counted_correctly(self) -> None:
        self._write("memory/reference/live.md", "---\nslug: live\n---\nbody\n")
        self._write("memory/reference/_shelf/shelved.md", "---\nslug: shelved\n---\nbody\n")
        self._write("memory/reference/_archive/archived.md", "---\nslug: archived\n---\nbody\n")

        entries = dream._iter_entries(self.vault)
        counts = dream._browse_surface_counts(self.vault, entries)

        self.assertEqual(counts["browse_live_count"], 1)
        self.assertEqual(counts["browse_shelved_count"], 1)
        self.assertEqual(counts["browse_archived_count"], 1)

    def test_archived_note_content_still_readable(self) -> None:
        # The acceptance test in the operator's own words: aged material
        # sits in the archive, still there on request -- never deleted.
        archived_path = self._write(
            "memory/reference/_archive/archived.md", "---\nslug: archived\n---\noriginal content\n"
        )
        entries = dream._iter_entries(self.vault)
        dream._browse_surface_counts(self.vault, entries)  # never mutates anything
        self.assertTrue(archived_path.is_file())
        self.assertIn("original content", archived_path.read_text(encoding="utf-8"))

    def test_empty_vault_reports_all_zero(self) -> None:
        entries = dream._iter_entries(self.vault)
        counts = dream._browse_surface_counts(self.vault, entries)
        self.assertEqual(counts["browse_live_count"], 0)
        self.assertEqual(counts["browse_shelved_count"], 0)
        self.assertEqual(counts["browse_archived_count"], 0)

    def test_counts_land_in_the_digest(self) -> None:
        self._write("memory/reference/live.md", "---\nslug: live\n---\nbody\n")
        self._write("memory/reference/_shelf/shelved.md", "---\nslug: shelved\n---\nbody\n")
        self._write("memory/reference/_archive/archived.md", "---\nslug: archived\n---\nbody\n")

        digest = dream.run_dream(self.vault, run_id="run-browse-1")
        digest_text = digest.digest_path.read_text(encoding="utf-8")

        self.assertIn("Browse surface:", digest_text)
        self.assertEqual(digest.corpus_stats["browse_live_count"], 1)
        self.assertEqual(digest.corpus_stats["browse_shelved_count"], 1)
        self.assertEqual(digest.corpus_stats["browse_archived_count"], 1)



class SupersededNotesStayOutOfTheStages(_DreamTestBase):
    """PLAN-superseded-vocabulary: a note the axis already settled is not a
    twin of its own successor, and never proposed again."""

    def test_a_superseded_note_is_never_matched_against_its_successor(self) -> None:
        body = "The server retries three times on timeout.\n"
        self._write("winner.md", "---\nslug: retries\nkind: fix\nstatus: active\n---\n" + body)
        self._write("loser.md", "---\nslug: retries-old\nkind: fix\nstatus: active\nlifecycle: superseded\nsuperseded_by: winner.md\n---\n" + body)
        digest = dream.run_dream(self.vault, run_id="run-superseded-skip")
        self.assertEqual([p for p in digest.proposals if p.stage == "dedup"], [])

    def test_an_archived_note_shares_no_key(self) -> None:
        self._write("a.md", "---\nslug: x\nlifecycle: archived\n---\nOption A.\n")
        self._write("b.md", "---\nslug: x\n---\nOption B.\n")
        digest = dream.run_dream(self.vault, run_id="run-archived-key")
        self.assertEqual([p for p in digest.proposals if p.stage == "contradiction_triage"], [])


if __name__ == "__main__":
    unittest.main()
