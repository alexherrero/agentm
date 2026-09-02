#!/usr/bin/env python3
"""Tests for crystallization's phase-close trigger (agentm-experience-and-
dreaming.md § Crystallization's phase-close trigger, locked calls 1-2-3-5-7-9).

Covers `crystallize.py`'s staging primitives (stage_candidate / list_candidates
/ drop_candidate / count_pending_candidates / exploration_judge_available) and
`orchestration_phase.py`'s resolver + dispatcher
(`_resolve_transcript_for_staging` / `stage_crystallization_candidate`) —
the sibling-step wiring itself is exercised end-to-end through the real CLI in
`scripts/verify-phases.sh`, not re-duplicated here.

Run: python3 scripts/test_crystallize_staging.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import auto_orchestration as ao  # noqa: E402
import crystallize  # noqa: E402
import orchestration_phase as op  # noqa: E402

_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(**over) -> dict:
    c = dict(ao.DEFAULT_CONFIG)
    c.update(over)
    return c


def _write_marker(project_root: Path, sid: str, transcript: str, *, suffix=".start") -> Path:
    d = project_root / ".harness"
    d.mkdir(parents=True, exist_ok=True)
    m = d / f"session-id-{sid}{suffix}"
    m.write_text(
        f"session_id: {sid}\nstarted_at: 2026-07-26T11:00:00Z\ntranscript: {transcript}\n",
        encoding="utf-8",
    )
    return m


class TestStageCandidate(unittest.TestCase):
    """crystallize.py's primitives, in isolation from any marker resolution."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_fire_stages_a_candidate_with_a_transcript_pointer(self) -> None:
        r = crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/t.jsonl")
        self.assertEqual(r["status"], "staged")
        self.assertEqual(r["fire_count"], 1)
        candidates = crystallize.list_candidates(self.vault)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["transcript"], "/tmp/t.jsonl")
        self.assertEqual(candidates[0]["session_id"], "sid-1")

    def test_refire_same_session_refreshes_not_duplicates(self) -> None:
        crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/t.jsonl", now="2026-07-26T11:00:00+00:00")
        r2 = crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/t.jsonl", now="2026-07-26T11:10:00+00:00")
        self.assertEqual(r2["status"], "refreshed")
        self.assertEqual(r2["fire_count"], 2)
        self.assertEqual(len(crystallize.list_candidates(self.vault)), 1)
        candidates = crystallize.list_candidates(self.vault)
        self.assertEqual(candidates[0]["first_fired"], "2026-07-26T11:00:00+00:00")
        self.assertEqual(candidates[0]["last_fired"], "2026-07-26T11:10:00+00:00")

    def test_post_work_and_post_release_are_distinct_candidates(self) -> None:
        crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/a.jsonl")
        crystallize.stage_candidate(self.vault, "post-release", "sid-1", "/tmp/a.jsonl")
        self.assertEqual(len(crystallize.list_candidates(self.vault)), 2)

    def test_pending_cap_skips_rather_than_grows(self) -> None:
        for i in range(crystallize._MAX_PENDING_CANDIDATES):
            r = crystallize.stage_candidate(self.vault, "post-work", f"sid-{i}", "/tmp/t.jsonl")
            self.assertEqual(r["status"], "staged")
        r_over = crystallize.stage_candidate(self.vault, "post-work", "sid-over", "/tmp/t.jsonl")
        self.assertEqual(r_over["status"], "capped")
        self.assertEqual(r_over["path"], None)
        self.assertEqual(
            len(crystallize.list_candidates(self.vault)), crystallize._MAX_PENDING_CANDIDATES
        )

    def test_cap_does_not_block_refreshing_an_existing_candidate(self) -> None:
        for i in range(crystallize._MAX_PENDING_CANDIDATES):
            crystallize.stage_candidate(self.vault, "post-work", f"sid-{i}", "/tmp/t.jsonl")
        r = crystallize.stage_candidate(self.vault, "post-work", "sid-0", "/tmp/t.jsonl")
        self.assertEqual(r["status"], "refreshed")
        self.assertEqual(r["fire_count"], 2)

    def test_drop_candidate_deletes_not_archives(self) -> None:
        crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/t.jsonl")
        self.assertTrue(crystallize.drop_candidate(self.vault, "post-work", "sid-1"))
        self.assertEqual(crystallize.list_candidates(self.vault), [])
        # No archive directory of any kind — only the staging dir itself, empty.
        self.assertEqual(list(crystallize._staging_dir(self.vault).iterdir()), [])

    def test_drop_missing_candidate_returns_false(self) -> None:
        self.assertFalse(crystallize.drop_candidate(self.vault, "post-work", "nope"))

    def test_count_pending_candidates_matches_list_length(self) -> None:
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 0)
        crystallize.stage_candidate(self.vault, "post-work", "sid-1", "/tmp/t.jsonl")
        crystallize.stage_candidate(self.vault, "post-release", "sid-2", "/tmp/t.jsonl")
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 2)

    def test_list_candidates_skips_malformed_file(self) -> None:
        staging = self.vault / crystallize.STAGING_DIRNAME
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "post-work-bad.json").write_text("not json", encoding="utf-8")
        crystallize.stage_candidate(self.vault, "post-work", "sid-good", "/tmp/t.jsonl")
        candidates = crystallize.list_candidates(self.vault)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["session_id"], "sid-good")

    def test_exploration_judge_available_is_false(self) -> None:
        # The named, unbuilt seam (call 7) — the chain cannot dispatch a model.
        self.assertFalse(crystallize.exploration_judge_available())


class TestStagingDirnameHasOneMeaning(unittest.TestCase):
    """The staging directory name exists as THREE independent copies: this
    module's `STAGING_DIRNAME`, and a hardcoded literal in each of the two push
    surfaces (`session_brief.count_crystallize_candidates`,
    `console.section_crystallize_candidates`). The surfaces keep their own
    literals deliberately — neither imports the memory-skill tree, and both
    must stay importable from a hook with no `sys.path` surgery.

    Copies drift. Found by mutation-testing this trigger: renaming
    `STAGING_DIRNAME` alone left both surfaces globbing the old path and
    silently reporting zero forever, and BOTH surface test suites still passed,
    because each writes its fixture at its own literal and asserts the count —
    a mirror that can only ever prove a surface agrees with itself. Only
    `verify-phases.sh`'s hardcoded path caught it, incidentally.

    So this pins the three together the same way `test_vault_lint.py` pins the
    three `_EXCLUDE_DIRS` sets: read the real value out of each module and
    assert they are one string.
    """

    def _load(self, rel_dir: str, module_name: str):
        path = _HERE.parent / rel_dir
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
        return __import__(module_name)

    def test_session_brief_agrees_with_staging_dirname(self) -> None:
        sb = self._load("scripts/health", "session_brief")
        import inspect
        src = inspect.getsource(sb.count_crystallize_candidates)
        self.assertIn(
            f'"{crystallize.STAGING_DIRNAME}"', src,
            "session_brief.count_crystallize_candidates no longer globs "
            f"{crystallize.STAGING_DIRNAME!r} — the brief will silently report "
            "zero staged candidates forever",
        )

    def test_console_agrees_with_staging_dirname(self) -> None:
        con = self._load("harness/skills/console/scripts", "console")
        import inspect
        src = inspect.getsource(con.section_crystallize_candidates)
        self.assertIn(
            f'"{crystallize.STAGING_DIRNAME}"', src,
            "console.section_crystallize_candidates no longer globs "
            f"{crystallize.STAGING_DIRNAME!r} — the console will silently "
            "report none staged forever",
        )

    def test_all_three_surfaces_see_the_same_staged_candidate(self) -> None:
        """The behavioral counterpart to the two source assertions above: stage
        one candidate through the real writer, then confirm both surfaces
        actually count it. This fails on a drift even if the literals are
        obfuscated past a source-text match."""
        sb = self._load("scripts/health", "session_brief")
        con = self._load("harness/skills/console/scripts", "console")
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            crystallize.stage_candidate(vault, "post-work", "sid-1", "/tmp/t.jsonl")
            self.assertEqual(crystallize.count_pending_candidates(vault), 1)
            self.assertEqual(sb.count_crystallize_candidates(vault), 1)
            self.assertIn("1 session(s) staged", con.section_crystallize_candidates(vault))


class TestResolveTranscriptForStaging(unittest.TestCase):
    """orchestration_phase._resolve_transcript_for_staging — the tolerant
    resolver call 3 requires, filtered per call 9's live-transcript rule."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.harness_dir = self.root / ".harness"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _live_transcript(self, name: str) -> Path:
        t = self.root / name
        t.write_text("", encoding="utf-8")
        return t

    def test_no_harness_dir(self) -> None:
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual((sid, transcript, reason), (None, None, "no-session"))

    def test_no_markers(self) -> None:
        self.harness_dir.mkdir()
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual(reason, "no-session")

    def test_single_start_marker_with_live_transcript(self) -> None:
        t = self._live_transcript("t.jsonl")
        _write_marker(self.root, "sid-1", str(t), suffix=".start")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertIsNone(reason)
        self.assertEqual(sid, "sid-1")
        self.assertEqual(transcript, str(t))

    def test_reflected_markers_are_ignored_entirely(self) -> None:
        """`.reflected` markers must not resolve, and must not count toward
        ambiguity. They accumulate for 30 days by design (their GC threshold),
        so an earlier version of this resolver that globbed them alongside
        `.start` made `ambiguous-session` the permanent steady state after just
        two reflected sessions — the trigger never fired at all. Staging runs
        before reflect instead, so a live session's `.start` is still there."""
        t = self._live_transcript("t.jsonl")
        _write_marker(self.root, "sid-1", str(t), suffix=".reflected")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual(reason, "no-session")

    def test_accumulated_reflected_history_does_not_block_a_live_session(self) -> None:
        """The regression test for the defect that shipped: a realistic repo
        carries many `.reflected` markers from past sessions, all with live
        transcripts. Exactly one of them is the session in progress, and it has
        a `.start`. Resolution must find it and ignore the history.

        This is the test whose absence let the trigger ship inert — every
        earlier fixture had one or two markers, so nothing modeled a repo with
        real history in it."""
        live = self._live_transcript("current.jsonl")
        for i in range(40):
            past = self._live_transcript(f"past-{i}.jsonl")
            _write_marker(self.root, f"sid-past-{i}", str(past), suffix=".reflected")
        _write_marker(self.root, "sid-current", str(live), suffix=".start")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertIsNone(reason, "accumulated .reflected history blocked a live session")
        self.assertEqual(sid, "sid-current")
        self.assertEqual(transcript, str(live))

    def test_dead_pointer_alone_is_no_session_not_ambiguous(self) -> None:
        _write_marker(self.root, "sid-dead", str(self.root / "gone.jsonl"), suffix=".start")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual(reason, "no-session")

    def test_dead_pointer_beside_one_live_marker_resolves_the_live_one(self) -> None:
        # Call 9's whole point: a pile of dead markers must not manufacture
        # false ambiguity for the one marker that actually resolves.
        t = self._live_transcript("t.jsonl")
        _write_marker(self.root, "sid-live", str(t), suffix=".start")
        _write_marker(self.root, "sid-dead", str(self.root / "gone.jsonl"), suffix=".start")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertIsNone(reason)
        self.assertEqual(sid, "sid-live")

    def test_two_live_markers_is_ambiguous(self) -> None:
        t1 = self._live_transcript("t1.jsonl")
        t2 = self._live_transcript("t2.jsonl")
        _write_marker(self.root, "sid-1", str(t1), suffix=".start")
        _write_marker(self.root, "sid-2", str(t2), suffix=".start")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual(reason, "ambiguous-session")

    def test_missing_transcript_line_is_treated_as_dead(self) -> None:
        marker = self.harness_dir / "session-id-sid-noline.start"
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("session_id: sid-noline\n", encoding="utf-8")
        sid, transcript, reason = op._resolve_transcript_for_staging(self.harness_dir)
        self.assertEqual(reason, "no-session")


class TestStageCrystallizationCandidate(unittest.TestCase):
    """orchestration_phase.stage_crystallization_candidate — the dispatcher
    _main() calls as a sibling step for both post-work and post-release."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.project_root = self.root / "proj"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _transcript(self, name: str = "t.jsonl") -> Path:
        t = self.root / name
        t.write_text("", encoding="utf-8")
        return t

    def test_disabled_is_skip(self) -> None:
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work",
            config=_cfg(enable_crystallization_staging=False), now=_NOW,
        )
        self.assertEqual(r["status"], "disabled")
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 0)

    def test_no_session_no_candidate(self) -> None:
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "no-session")

    def test_stages_a_candidate_carrying_a_transcript_pointer(self) -> None:
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "staged")
        candidates = crystallize.list_candidates(self.vault)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["transcript"], str(t))
        self.assertEqual(candidates[0]["phase"], "post-work")

    def test_second_dispatch_same_session_refreshes_not_duplicates(self) -> None:
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t))
        op.stage_crystallization_candidate(self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW)
        r2 = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW + timedelta(minutes=5),
        )
        self.assertEqual(r2["status"], "refreshed")
        self.assertEqual(r2["fire_count"], 2)
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 1)

    def test_later_task_commits_skip_harmlessly(self) -> None:
        """After reflect has renamed a session's marker, staging finds no
        `.start` and skips. That is intended, not a regression: the candidate
        was already written on the first task commit (staging runs before
        reflect), and `stage_candidate` is idempotent, so the only thing a later
        commit would have added is a `fire_count` bump — deliberately traded
        away to keep accumulated `.reflected` history from making every
        resolution ambiguous."""
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t), suffix=".reflected")
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "no-session")

    def test_post_release_stages_too(self) -> None:
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-release", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "staged")
        candidates = crystallize.list_candidates(self.vault)
        self.assertEqual(candidates[0]["phase"], "post-release")

    def test_pending_cap_reported_as_capped(self) -> None:
        for i in range(crystallize._MAX_PENDING_CANDIDATES):
            crystallize.stage_candidate(self.vault, "post-work", f"filler-{i}", "/tmp/t.jsonl")
        t = self._transcript()
        _write_marker(self.project_root, "sid-new", str(t))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "capped")

    def test_dry_run_resolves_but_writes_nothing(self) -> None:
        t = self._transcript()
        _write_marker(self.project_root, "sid-1", str(t))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW, dry_run=True,
        )
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual(r["session_id"], "sid-1")
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 0)

    def test_ambiguous_session_no_candidate(self) -> None:
        t1 = self._transcript("t1.jsonl")
        t2 = self._transcript("t2.jsonl")
        _write_marker(self.project_root, "sid-1", str(t1))
        _write_marker(self.project_root, "sid-2", str(t2))
        r = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW,
        )
        self.assertEqual(r["status"], "ambiguous-session")
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 0)

    def test_no_cooldown_unlike_reflect(self) -> None:
        # Call 5: staging fires on every dispatch — no cooldown gate. Two
        # dispatches for two DIFFERENT sessions in immediate succession both
        # stage, unlike phase_reflect's shared cooldown.
        t1 = self._transcript("t1.jsonl")
        _write_marker(self.project_root, "sid-1", str(t1))
        r1 = op.stage_crystallization_candidate(self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW)
        self.assertEqual(r1["status"], "staged")
        (self.project_root / ".harness" / "session-id-sid-1.start").unlink()
        t2 = self._transcript("t2.jsonl")
        _write_marker(self.project_root, "sid-2", str(t2))
        r2 = op.stage_crystallization_candidate(
            self.vault, self.project_root, "post-work", config=_cfg(), now=_NOW + timedelta(seconds=1),
        )
        self.assertEqual(r2["status"], "staged")
        self.assertEqual(crystallize.count_pending_candidates(self.vault), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
