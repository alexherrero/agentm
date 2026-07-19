#!/usr/bin/env python3
"""Unit tests for `lint.py` — the weekly + on-demand lint engine
(auto-organization part 3, task 7).

Task 7 verification: a fixture corpus with seeded rot (an orphan, a
mis-cased broken link, a genuinely-broken link, a contradiction, a note
outside the kind registry) produces exactly the expected report — the
mis-cased link auto-repairs, the genuinely broken one surfaces instead,
the contradiction surfaces without any auto-resolution, and the
out-of-registry kind appears in the report. `lint.main()` (the
`/memory lint` CLI) run against the same fixture matches
`dream._stage_lint()`'s output exactly.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import lint  # noqa: E402
import dream  # noqa: E402


_CLEAN_FM = (
    "kind: {kind}\n"
    "status: {status}\n"
    "created: 2025-01-01\n"
    "updated: 2025-01-01\n"
    "tags: []\n"
    "group: personal\n"
    "slug: {slug}\n"
    "always_load: false\n"
)


def _note(slug: str, *, kind: str = "reference", status: str = "active", extra_fm: str = "", body: str = "Body.\n") -> str:
    fm = _CLEAN_FM.format(kind=kind, status=status, slug=slug) + extra_fm
    return f"---\n{fm}---\n\n{body}"


class _LintFixtureTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def _write(self, name: str, content: str) -> Path:
        path = self.vault / "personal" / "reference" / name
        path.write_text(content, encoding="utf-8")
        return path

    def _seed_rot(self) -> None:
        # 1. An orphan: no links to or from it.
        self._write("orphan.md", _note("orphan"))

        # 2. A note with the mis-cased-but-uniquely-resolvable target.
        self._write("correct-target.md", _note("correct-target"))
        self._write("miscased-source.md", _note("miscased-source", body="See [[Correct-Target]] for detail.\n"))

        # 3. A note with a genuinely broken link (no match at all, cased or not).
        self._write("broken-source.md", _note("broken-source", body="See [[Nonexistent-Note]] for detail.\n"))

        # 4. A contradiction: status: superseded with no backing lineage.
        self._write("dangling-superseded.md", _note("dangling-superseded", status="superseded"))

        # 5. A note outside the kind registry.
        self._write("mystery-kind.md", _note("mystery-kind", kind="totally-made-up-kind"))


class SeededRotFixtureTests(_LintFixtureTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._seed_rot()
        self.report = lint.run_lint(self.vault)

    def test_orphan_reported(self) -> None:
        self.assertIn("personal/reference/orphan.md", self.report.orphans)

    def test_miscased_link_auto_repairs_not_surfaced_as_error(self) -> None:
        self.assertEqual(len(self.report.repairs), 1)
        entry, old_raw, new_raw = self.report.repairs[0]
        self.assertEqual(entry.rel, "personal/reference/miscased-source.md")
        self.assertIn("[[Correct-Target]]", old_raw)
        self.assertIn("[[correct-target]]", new_raw)

        errors = [f for f in self.report.findings if f.severity == "error" and f.entry_path == entry.rel]
        self.assertEqual(errors, [])
        info = [f for f in self.report.findings if f.check_id == "wikilink-resolution" and f.entry_path == entry.rel]
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0].severity, "info")
        self.assertIn("auto-corrected", info[0].message)

    def test_genuinely_broken_link_surfaces_never_repaired(self) -> None:
        repaired_rels = {entry.rel for entry, _old, _new in self.report.repairs}
        self.assertNotIn("personal/reference/broken-source.md", repaired_rels)
        broken = [
            f for f in self.report.findings
            if f.check_id == "wikilink-resolution" and f.entry_path == "personal/reference/broken-source.md"
        ]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].severity, "error")

    def test_contradiction_surfaces_without_auto_resolution(self) -> None:
        dangling = [f for f in self.report.findings if f.check_id == "dangling-supersession"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0].entry_path, "personal/reference/dangling-superseded.md")
        self.assertEqual(dangling[0].severity, "warn")
        self.assertEqual(self.report.contradiction_count, 1)
        # No mutation was ever proposed for it -- surfaced only.
        repaired_rels = {entry.rel for entry, _old, _new in self.report.repairs}
        self.assertNotIn("personal/reference/dangling-superseded.md", repaired_rels)

    def test_out_of_registry_kind_appears_in_report(self) -> None:
        kt = [f for f in self.report.findings if f.check_id == "kind-taxonomy"]
        self.assertEqual(len(kt), 1)
        self.assertEqual(kt[0].entry_path, "personal/reference/mystery-kind.md")


class GraphSnapshotCrossCheckTests(_LintFixtureTestBase):
    """Task 9 verification: the graph-snapshot cross-check catches drift
    between the persisted snapshot and a live re-extraction. This must
    run BEFORE `run_lint()`'s own snapshot rebuild — a note whose
    snapshot was never built (or is stale) has to be caught in THAT same
    call, not silently healed by the rebuild moments before the check
    ever looks. A second call against the same (now-rebuilt) vault finds
    nothing, proving the check is real and not a permanent false-positive
    generator either."""

    def test_never_indexed_note_with_a_real_link_is_caught_on_first_scan(self) -> None:
        self._write("correct-target.md", _note("correct-target"))
        self._write("linking-note.md", _note("linking-note", body="See [[correct-target]] for detail.\n"))

        report = lint.run_lint(self.vault)

        drift = [f for f in report.findings if f.check_id == "graph-snapshot-drift"]
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0].entry_path, "personal/reference/linking-note.md")
        self.assertEqual(drift[0].severity, "warn")

    def test_second_scan_after_the_rebuild_finds_no_drift(self) -> None:
        self._write("correct-target.md", _note("correct-target"))
        self._write("linking-note.md", _note("linking-note", body="See [[correct-target]] for detail.\n"))

        lint.run_lint(self.vault)  # first scan: catches the drift, then rebuilds
        second_report = lint.run_lint(self.vault)  # second scan: already fresh

        drift = [f for f in second_report.findings if f.check_id == "graph-snapshot-drift"]
        self.assertEqual(drift, [])

    def test_rotation_eventually_attempts_every_entry_not_just_the_first_batch(self) -> None:
        # Adversarial-review regression: without a persisted rotation
        # cursor, `sorted(...)[:_CAP]` re-selects the SAME alphabetically-
        # first batch every cycle forever, permanently starving any entry
        # past the cap -- it would never even be LOOKED AT, regardless of
        # whether it happens to carry real drift. 30 filler notes (past
        # the 25-entry cap) plus one sorting alphabetically LAST: proves
        # the rotation reaches it within a bounded number of cycles.
        for i in range(30):
            self._write(f"filler-{i:02d}.md", _note(f"filler-{i:02d}"))
        self._write("zzz-last.md", _note("zzz-last"))

        reached = False
        for _ in range(3):  # ceil(31 / 25) = 2 cycles needed; generous headroom
            lint.run_lint(self.vault)
            attempted = json.loads(
                (self.vault / "_meta" / "graph-snapshot-cross-check-state.json").read_text(encoding="utf-8")
            )["attempted"]
            if "personal/reference/zzz-last.md" in attempted:
                reached = True
                break
        self.assertTrue(reached, "an entry past the batch cap was never reached by the rotation")

    def test_drift_introduced_between_cycles_is_caught_when_the_rotation_reaches_it(self) -> None:
        # A more realistic drift scenario than "brand new, never indexed":
        # cycle 1 establishes a clean baseline for the WHOLE corpus (every
        # note is new, so the unconditional rebuild indexes everything
        # regardless of what the cross-check itself sampled). Content is
        # THEN changed directly -- mimicking a mutation path that doesn't
        # call graph_snapshot.rebuild() itself (e.g. tidying/dedup moves)
        # -- so genuine drift exists at the START of cycle 2, for an entry
        # the rotation has now reached.
        for i in range(30):
            self._write(f"filler-{i:02d}.md", _note(f"filler-{i:02d}"))
        self._write("zzz-target.md", _note("zzz-target"))
        zzz_path = self._write("zzz-linking-note.md", _note("zzz-linking-note"))

        lint.run_lint(self.vault)  # cycle 1: clean baseline, whole corpus indexed

        zzz_path.write_text(
            zzz_path.read_text(encoding="utf-8").rstrip("\n") + "\n\nSee [[zzz-target]] for detail.\n",
            encoding="utf-8",
        )

        found = False
        for _ in range(3):  # the rotation needs at most 1 more cycle to reach zzz-linking-note
            report = lint.run_lint(self.vault)
            drift = {f.entry_path for f in report.findings if f.check_id == "graph-snapshot-drift"}
            if "personal/reference/zzz-linking-note.md" in drift:
                found = True
                break
        self.assertTrue(found, "drift introduced between cycles was never caught once the rotation reached it")

    def test_pool_exhaustion_resets_the_pass(self) -> None:
        # Once every entry has had a turn, the NEXT cycle starts a fresh
        # pass rather than sampling nothing forever -- same contract as
        # dream.py's own link-backfill pool exhaustion.
        for i in range(30):
            self._write(f"filler-{i:02d}.md", _note(f"filler-{i:02d}"))

        first = lint.run_lint(self.vault)
        self.assertEqual(len(first.model.entries), 30)

        second = lint.run_lint(self.vault)  # drains the remaining 5
        self.assertIsNotNone(second)
        attempted_after_second = json.loads(
            (self.vault / "_meta" / "graph-snapshot-cross-check-state.json").read_text(encoding="utf-8")
        )["attempted"]
        # After exactly 2 cycles the whole 30-entry corpus has had a turn.
        self.assertEqual(len(attempted_after_second), 30)

        third = lint.run_lint(self.vault)  # fresh pass: samples from the start again
        self.assertIsNotNone(third)  # the third call itself must not raise/crash on a fresh pass

    def test_stage_lint_runs_before_link_improvement_rebuilds_in_run_dream(self) -> None:
        # The real regression this guards: run_dream()'s own
        # _stage_link_improvement unconditionally rebuilds the snapshot
        # in full-vault mode as a side effect. If _stage_lint ran AFTER
        # it in the same cycle, the cross-check would compare a snapshot
        # against itself moments after its own rebuild and never find
        # anything -- through the real weekly pipeline, not just
        # run_lint() in isolation.
        self._write("correct-target.md", _note("correct-target"))
        self._write("linking-note.md", _note("linking-note", body="See [[correct-target]] for detail.\n"))

        digest = dream.run_dream(self.vault, run_id="run-cross-check")
        self.assertEqual(digest.corpus_stats.get("lint_graph_snapshot_mismatch_count"), 1)


class FencedCodeRepairSafetyTests(_LintFixtureTestBase):
    """Adversarial-review regression: a mis-cased wikilink shown inside a
    fenced code block as a documentation example must never be auto-
    repaired — it isn't a real link. Before the fix, `_build_repairs` did
    a blind whole-file string `.replace()`, which rewrote every literal
    occurrence including ones inside fences."""

    def test_fenced_example_left_untouched_real_link_still_repaired(self) -> None:
        self._write("correct-target.md", _note("correct-target"))
        body = (
            "See [[Correct-Target]] for detail.\n\n"
            "```\n"
            "[[Correct-Target]] is how you write a wikilink to this note.\n"
            "```\n"
        )
        self._write("mixed-source.md", _note("mixed-source", body=body))

        report = lint.run_lint(self.vault)

        self.assertEqual(len(report.repairs), 1)
        entry, old_raw, new_raw = report.repairs[0]
        self.assertEqual(entry.rel, "personal/reference/mixed-source.md")
        # The prose occurrence was repaired...
        self.assertEqual(
            new_raw.count("See [[correct-target]] for detail."), 1,
        )
        # ...but the fenced documentation example was left byte-identical.
        self.assertIn(
            "```\n[[Correct-Target]] is how you write a wikilink to this note.\n```\n",
            new_raw,
        )

    def test_wikilink_entirely_inside_a_fence_is_never_repaired(self) -> None:
        self._write("correct-target.md", _note("correct-target"))
        body = "```\nExample: [[Correct-Target]]\n```\n"
        self._write("fenced-only.md", _note("fenced-only", body=body))

        report = lint.run_lint(self.vault)

        repaired_rels = {entry.rel for entry, _old, _new in report.repairs}
        self.assertNotIn("personal/reference/fenced-only.md", repaired_rels)


class AliasedCollisionFindingTests(_LintFixtureTestBase):
    """Adversarial-review regression: an aliased occurrence of the same
    wrong-cased target as a repaired bare occurrence must stay a
    genuinely-broken finding, not be silently suppressed. Before the fix,
    the finding filter matched on message-substring alone, and
    `check_wikilinks` renders an identical message for both (the alias is
    stripped before rendering) -- dropping "any" match discarded the
    still-broken aliased finding too."""

    def test_aliased_occurrence_stays_broken_after_bare_occurrence_repairs(self) -> None:
        self._write("correct-target.md", _note("correct-target"))
        body = (
            "See [[Correct-Target]] for detail.\n\n"
            "Also see [[Correct-Target|a different alias]] elsewhere.\n"
        )
        self._write("both-forms.md", _note("both-forms", body=body))

        report = lint.run_lint(self.vault)

        self.assertEqual(len(report.repairs), 1)
        _entry, _old_raw, new_raw = report.repairs[0]
        # The bare occurrence was repaired...
        self.assertIn("See [[correct-target]] for detail.", new_raw)
        # ...but the aliased occurrence was never touched -- still
        # literally mis-cased on disk.
        self.assertIn("[[Correct-Target|a different alias]]", new_raw)

        # The report must still carry an ERROR for the aliased occurrence
        # -- it was never repaired, so it must never read as resolved.
        errors = [
            f for f in report.findings
            if f.severity == "error" and f.entry_path == "personal/reference/both-forms.md"
        ]
        self.assertEqual(len(errors), 1)
        # Exactly one info note for the repaired occurrence, not zero and not two.
        infos = [
            f for f in report.findings
            if f.severity == "info" and f.entry_path == "personal/reference/both-forms.md"
        ]
        self.assertEqual(len(infos), 1)


class CliAndWeeklyStageParityTests(_LintFixtureTestBase):
    """The plan's own parity requirement: `/memory lint` run manually
    against a fixture matches `dream._stage_lint()`'s output exactly."""

    def test_stage_lint_and_cli_report_agree(self) -> None:
        self._seed_rot()

        cli_report = lint.run_lint(self.vault)
        proposals, stats = dream._stage_lint(self.vault)

        self.assertEqual(len(proposals), len(cli_report.repairs))
        self.assertEqual(proposals[0].stage, "lint")
        self.assertEqual(proposals[0].kind, "wikilink_repair")
        self.assertEqual(proposals[0].paths, ["personal/reference/miscased-source.md"])

        self.assertEqual(stats["lint_orphan_count"], len(cli_report.orphans))
        self.assertEqual(stats["lint_contradiction_count"], cli_report.contradiction_count)
        self.assertEqual(stats["lint_mean_quality_score"], cli_report.mean_quality_score)

    def test_cli_apply_writes_the_identical_content_the_stage_would_propose(self) -> None:
        self._seed_rot()
        proposals, _stats = dream._stage_lint(self.vault)
        expected_path, expected_content = proposals[0].mutations[0]

        rc = lint.main(["--vault-path", str(self.vault), "--apply"])
        self.assertEqual(rc, 0)
        self.assertEqual(expected_path.read_text(encoding="utf-8"), expected_content)


class WeeklyAutoApplyIntegrationTests(_LintFixtureTestBase):
    """The lint stage's mis-cased-wikilink repair auto-applies through the
    real `run_dream_and_auto_apply()` pipeline, revert-logged like every
    other auto-apply stage; everything else it reports stays advisory."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402

        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def test_miscased_repair_auto_applies_and_reverts(self) -> None:
        self._seed_rot()
        source_path = self.vault / "personal" / "reference" / "miscased-source.md"

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-lint-1", revert_log=self.revert_log,
        )

        lint_items = [i for i in batch.items if i["stage"] == "lint"]
        self.assertEqual(len(lint_items), 1)
        self.assertIn("[[correct-target]]", source_path.read_text(encoding="utf-8"))
        self.assertNotIn("[[Correct-Target]]", source_path.read_text(encoding="utf-8"))

        self.revert_log.revert("run-lint-1", entry_id=lint_items[0]["entry_id"])
        self.assertIn("[[Correct-Target]]", source_path.read_text(encoding="utf-8"))

    def test_digest_renders_lint_summary_line(self) -> None:
        self._seed_rot()
        digest, _batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-lint-2", revert_log=self.revert_log,
        )
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Lint:", digest_text)
        self.assertIn("orphan(s)", digest_text)
        self.assertIn("contradiction(s)", digest_text)
        self.assertIn("mean quality score", digest_text)


if __name__ == "__main__":
    unittest.main()
