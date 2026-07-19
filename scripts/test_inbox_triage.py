#!/usr/bin/env python3
"""Unit tests for `inbox_triage.py` — the `_inbox/` bulk-review pass
(`/memory inbox --bulk-review`).

`inbox_triage.py` lives in `harness/skills/memory/scripts/` (same cross-dir
import pattern as `test_dream.py` / `test_dream_confirm.py` /
`test_watchlist_review.py`).

Covers the operator's original 2026-07-11 verification list, PLUS the
second-pass confirm-gate retirement (also 2026-07-11 -- the operator
personally confirmed the entire 635-proposal/1,565-entry first-run
backlog with zero errors, then ruled "moving forward no need to confirm
gate the inbox"):
  - the cutover-marker logic correctly classifies pre-existing vs.
    post-cutover entries -- still real, now purely informational
    (`CutoverMarkerTests`)
  - a mixed-disposition first pass over a fixture backlog now auto-applies
    EVERY proposal, fully revertible (`BacklogAutoAppliesByDefaultTests`,
    supersedes the old confirm-gated-first-pass test)
  - promote- and merge-eligible dispositions on pre-existing-backlog-shaped
    entries (no `created` field) auto-apply without confirmation
    (`BacklogPromoteAndMergeAutoApplyTests`)
  - post-cutover expire, promote, and merge proposals ALL auto-apply
    together in the same run -- no sibling proposal is left pending on
    account of its disposition (`PostCutoverAutoApplyTests`)
  - the 255-survivor scenario: a no-`created` entry left at `status: inbox`
    from a prior run (the "kept" side of an earlier merge) gets triaged
    and auto-applied on a later run instead of staying stuck forever
    (`SurvivorRetriageTests`)
  - promote reuses the real curated-destination convention
    (`save._build_frontmatter` / `save.save_entry()`'s path+frontmatter
    shape) (`PromoteReusesCanonicalConventionTests`)
  - merge reuses the real dedup-similarity logic (`dream._stage_dedup`,
    same threshold, same merge shape) (`MergeReusesDedupLogicTests`)
  - the CLI's staging/confirm/revert flow round-trips correctly, and the
    reject path stops a re-proposal on the next scan (`ReviewFlowTests`)
  - the manual `--list` / `--confirm` / `--reject` CLI paths still work
    for inspecting or intervening on a specific proposal by hand, and
    `--no-auto-apply` still proposes without applying anything
    (`ManualCliPathsTests`)
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream_confirm as dc  # noqa: E402
import inbox_triage as it  # noqa: E402
from revert_log import RevertLog  # noqa: E402


class _TTYStringIO(io.StringIO):
    """A StringIO whose `isatty()` reports True -- `review_inbox_triage`
    (like `watchlist_review.review_watchlist`) defaults every prompt to
    skip on non-TTY stdin, so exercising the interactive confirm/reject
    branches needs a stdin that claims to be a real terminal."""

    def isatty(self) -> bool:  # noqa: D401
        return True


_INBOX_TEMPLATE = (
    "---\n"
    "kind: {kind}\n"
    "status: inbox\n"
    "{created_line}"
    "slug: {slug}\n"
    "mining_confidence: {confidence}\n"
    "mining_rationale: \"test fixture\"\n"
    "mining_occurrences: {occurrences}\n"
    "---\n\n"
    "{body}\n"
)


class _ConfidentMergeVerdict:
    def __enter__(self):
        self._p1 = mock.patch.object(it.dream, "cheap_model_tier_available", return_value=True)
        self._p2 = mock.patch.object(it, "judge_fuzzy_merge", return_value="yes")
        self._p1.__enter__()
        self._p2.__enter__()
        return self

    def __exit__(self, *exc):
        self._p2.__exit__(*exc)
        self._p1.__exit__(*exc)
        return False


class _InboxTriageTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "personal" / "_inbox").mkdir(parents=True)
        self.log_root = self.root / "revert-log"
        self.lock_root = self.root / "locks"
        self.revert_log = RevertLog(self.vault, log_root=self.log_root, lock_root=self.lock_root)

    def _inbox_dir(self) -> Path:
        return self.vault / "personal" / "_inbox"

    def _write_inbox(
        self, slug: str, *, kind: str = "idea", confidence: str = "LOW",
        occurrences: int = 1, body: str = "An unreinforced hunch.", created: str | None = None,
    ) -> Path:
        created_line = f"created: {created}\n" if created else ""
        content = _INBOX_TEMPLATE.format(
            kind=kind, created_line=created_line, slug=slug, confidence=confidence,
            occurrences=occurrences, body=body,
        )
        path = self._inbox_dir() / f"{slug}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def _status(self, path: Path) -> str | None:
        return it._current_status(path)


# -----------------------------------------------------------------------------
# Cutover marker
# -----------------------------------------------------------------------------

class CutoverMarkerTests(_InboxTriageTestBase):
    def test_first_call_stamps_marker_and_later_calls_never_overwrite_it(self) -> None:
        now0 = 1_000_000.0
        first = it.ensure_cutover_marker(self.vault, now=now0)
        self.assertTrue((self.vault / "_meta" / "inbox-triage-cutover.json").exists())

        later = it.ensure_cutover_marker(self.vault, now=now0 + 999_999.0)
        self.assertEqual(first, later, "the cutover stamp must never move once set")

    def test_entry_with_no_created_field_is_always_pre_existing_backlog(self) -> None:
        cutover_at = it._utcnow_iso(2_000_000.0)
        self.assertTrue(it._is_pre_existing_backlog({}, cutover_at))

    def test_entry_created_before_or_at_cutover_is_pre_existing_backlog(self) -> None:
        cutover_at = it._utcnow_iso(2_000_000.0)
        before = it._utcnow_iso(1_000_000.0)
        exactly_at = cutover_at
        self.assertTrue(it._is_pre_existing_backlog({"created": before}, cutover_at))
        self.assertTrue(it._is_pre_existing_backlog({"created": exactly_at}, cutover_at))

    def test_entry_created_strictly_after_cutover_is_not_backlog(self) -> None:
        cutover_at = it._utcnow_iso(2_000_000.0)
        after = it._utcnow_iso(3_000_000.0)
        self.assertFalse(it._is_pre_existing_backlog({"created": after}, cutover_at))


# -----------------------------------------------------------------------------
# A mixed-disposition first pass auto-applies everything (2026-07-11,
# second pass -- supersedes the old confirm-gated-first-pass contract:
# the operator personally confirmed the entire first-run backlog with zero
# errors, then retired the confirm-gate going forward).
# -----------------------------------------------------------------------------

class BacklogAutoAppliesByDefaultTests(_InboxTriageTestBase):
    def test_first_pass_over_a_mixed_backlog_auto_applies_every_disposition(self) -> None:
        # A merge pair, an occurrence-reinforced promote candidate, and a
        # stale unreinforced entry -- one of each disposition, all with no
        # `created` field (the real legacy-backlog shape). Under the
        # retired confirm-gate, ALL of these used to stay pending forever;
        # now they all auto-apply, fully revertible.
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        self._write_inbox("c", kind="preferences", occurrences=5, body="Always use tabs not spaces.")
        self._write_inbox("d", body="A completely unrelated single hunch.")

        now0 = time.time()
        with _ConfidentMergeVerdict():
            digest, batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=now0, revert_log=self.revert_log, lock_root=self.lock_root,
            )

        self.assertGreaterEqual(len(digest.proposals), 3)
        self.assertEqual(
            len(batch.items), len(digest.proposals),
            "confirm-gating is retired -- every proposal this run must auto-apply, first pass included",
        )

        pending = dc.list_pending(self.vault, digest.run_id)
        self.assertTrue(pending, "every proposal must exist")
        for p in pending:
            self.assertEqual(p.status, "confirmed", f"proposal #{p.index} ({p.stage}) must have auto-applied")

        # Every disposition this pipeline proposes is represented, and all
        # of them -- including the backlog-era expire stage, which never
        # used to auto-apply -- are in the applied set.
        applied_stages = {item["stage"] for item in batch.items}
        self.assertIn(it.MERGE_STAGE, applied_stages)
        self.assertIn(it.PROMOTE_STAGE, applied_stages)
        self.assertIn(
            it.BACKLOG_EXPIRE_STAGE, applied_stages,
            "no entry here has a created field, so its expire proposal stages as backlog -- and must still auto-apply",
        )

        # Every applied mutation is still fully revertible, individually,
        # via the same RevertLog instance -- reversibility is unchanged,
        # only the gate in front of it is gone.
        for item in batch.items:
            self.revert_log.revert(digest.run_id, item["entry_id"])
        for slug in ("a", "b", "c", "d"):
            self.assertEqual(
                self._status(self._inbox_dir() / f"{slug}.md"), "inbox",
                "revert must restore every entry's original status",
            )


# -----------------------------------------------------------------------------
# Post-cutover: expire, promote, and merge ALL auto-apply together now --
# no sibling proposal is left pending on account of its disposition
# (2026-07-11, second pass).
# -----------------------------------------------------------------------------

class PostCutoverAutoApplyTests(_InboxTriageTestBase):
    def test_expire_promote_and_merge_all_auto_apply_together(self) -> None:
        now0 = time.time()
        cutover_at = it.ensure_cutover_marker(self.vault, now=now0)
        self.assertIsNotNone(cutover_at)

        created_new = it._utcnow_iso(now0 + 10)  # strictly after cutover
        self._write_inbox("stale", created=created_new, body="A fresh but unreinforced hunch.")
        self._write_inbox(
            "dup1", created=created_new,
            body="Near duplicate pair one text here for merge testing purposes today.",
        )
        self._write_inbox(
            "dup2", created=created_new,
            body="Near duplicate pair one text here for merge testing purposes today!",
        )
        self._write_inbox(
            "reinforced", created=created_new, kind="workflow", occurrences=4,
            body="Always run tests before pushing.",
        )

        now1 = now0 + (200 * 86400)  # 200 days later -- past the 90d default TTL
        with _ConfidentMergeVerdict():
            digest, batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=now1, revert_log=self.revert_log, lock_root=self.lock_root,
            )

        by_stage = {p.stage for p in digest.proposals}
        self.assertIn(it.AUTO_APPLY_ELIGIBLE_STAGE, by_stage)
        self.assertIn(it.MERGE_STAGE, by_stage)
        self.assertIn(it.PROMOTE_STAGE, by_stage)

        # All three dispositions auto-applied together -- disposition kind
        # (and the entry's cutover era) no longer decides eligibility.
        applied_stages = {item["stage"] for item in batch.items}
        self.assertEqual(applied_stages, {it.AUTO_APPLY_ELIGIBLE_STAGE, it.MERGE_STAGE, it.PROMOTE_STAGE})
        self.assertEqual(len(batch.items), len(digest.proposals))
        self.assertEqual(self._status(self._inbox_dir() / "stale.md"), "expired")
        # dream._stage_dedup's merge mutation: the hub (dup1, alphabetically
        # first) keeps `status: inbox` in its own frontmatter -- only its
        # body changes to absorb dup2's content; dup2 (the match) is the
        # one that gets patched `status: superseded`.
        self.assertEqual(self._status(self._inbox_dir() / "dup2.md"), "superseded")
        dup1_body = (self._inbox_dir() / "dup1.md").read_text(encoding="utf-8")
        self.assertIn("merge testing purposes today!", dup1_body, "the merge mutation must have applied (body absorbed dup2's content)")
        self.assertEqual(self._status(self._inbox_dir() / "reinforced.md"), "promoted")

        # Nothing is left pending.
        pending = dc.list_pending(self.vault, digest.run_id)
        for p in pending:
            self.assertEqual(p.status, "confirmed")

    def test_backlog_expire_now_auto_applies_past_ttl_too(self) -> None:
        # Supersedes the old "backlog expire never auto-applies" contract:
        # a pre-existing-backlog-shaped entry (no `created` field) is
        # exactly the shape the operator's second ruling targeted --
        # it must auto-apply now, not stay confirm-gated forever.
        now0 = time.time()
        it.ensure_cutover_marker(self.vault, now=now0)
        self._write_inbox("old", body="No created field at all -- pre-existing backlog.")

        now1 = now0 + (365 * 86400)
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now1, revert_log=self.revert_log, lock_root=self.lock_root,
        )
        stages = {p.stage for p in digest.proposals}
        self.assertIn(it.BACKLOG_EXPIRE_STAGE, stages)
        applied_stages = {item["stage"] for item in batch.items}
        self.assertIn(it.BACKLOG_EXPIRE_STAGE, applied_stages, "backlog-era expire must auto-apply under the retired confirm-gate")
        self.assertEqual(self._status(self._inbox_dir() / "old.md"), "expired")


# -----------------------------------------------------------------------------
# Promote reuses the real curated-destination convention
# -----------------------------------------------------------------------------

class PromoteReusesCanonicalConventionTests(_InboxTriageTestBase):
    def test_promote_writes_to_the_same_path_and_frontmatter_shape_save_entry_uses(self) -> None:
        self._write_inbox(
            "h", kind="workflow", occurrences=4,
            body="Always run tests before pushing.\n\n## Mining metadata\n\n- **Category**: `workflow`\n",
        )
        digest = it.run_inbox_triage(self.vault, now=time.time())
        self.assertEqual(len(digest.proposals), 1)
        self.assertEqual(digest.proposals[0].stage, it.PROMOTE_STAGE)

        stdin = _TTYStringIO("c\n")
        stats = it.review_inbox_triage(self.vault, digest.run_id, self.revert_log, stdin=stdin, stdout=io.StringIO())
        self.assertEqual(stats["confirmed"], 1)

        canonical = self.vault / "personal" / "workflow" / "h.md"
        self.assertTrue(canonical.exists(), "promote must land at <vault>/<group>/<kind>/<slug>.md, save.py's own convention")
        fm, body = it._parse_frontmatter(canonical.read_text(encoding="utf-8"))
        # Same locked field order save._build_frontmatter emits.
        self.assertEqual(fm["kind"], "workflow")
        self.assertEqual(fm["status"], "active")
        self.assertEqual(fm["group"], "personal")
        self.assertEqual(fm["slug"], "h")
        self.assertIn("personal/_inbox/h.md", fm["derived_from"])
        self.assertIn("Always run tests before pushing.", body)
        self.assertNotIn("Mining metadata", body, "the triage instrumentation must not leak into the canonical entry")

        # The source inbox entry is retired, not deleted.
        self.assertTrue((self._inbox_dir() / "h.md").exists())
        self.assertEqual(self._status(self._inbox_dir() / "h.md"), "promoted")

    def test_promote_skips_rather_than_overwrites_an_existing_canonical_collision(self) -> None:
        collision = self.vault / "personal" / "fix" / "dup-slug.md"
        collision.parent.mkdir(parents=True)
        collision.write_text("---\nkind: fix\nslug: dup-slug\n---\n\npre-existing canonical entry\n", encoding="utf-8")

        self._write_inbox("dup-slug", kind="fix", occurrences=5, body="A reinforced hunch that collides.")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        # No promote proposal was staged for the colliding slug (skipped, never overwritten).
        promote_paths = [p.paths for p in digest.proposals if p.stage == it.PROMOTE_STAGE]
        self.assertEqual(promote_paths, [])
        self.assertIn("pre-existing canonical entry", collision.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# Merge reuses the real dedup-similarity logic
# -----------------------------------------------------------------------------

class MergeReusesDedupLogicTests(_InboxTriageTestBase):
    def test_near_duplicate_pair_detected_at_dream_similarity_threshold(self) -> None:
        # Same detection intent as ever (dream's difflib threshold finds
        # the pair) -- but under the verdict-gated contract (auto-org part
        # 3 task 3, the plan's Locked design call) an UNVERDICTED fuzzy
        # pair routes to needs-your-eye instead of an auto-applied merge.
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(merges, [])
        self.assertEqual(len(digest.needs_your_eye), 1)
        self.assertEqual(
            set(Path(p).name for p in digest.needs_your_eye[0]["paths"]), {"a.md", "b.md"}
        )
        # With a confident verdict, the same still-untriaged pair (nothing
        # was applied above) merges exactly as before.
        with _ConfidentMergeVerdict():
            digest2 = it.run_inbox_triage(self.vault, now=time.time())
        merges2 = [p for p in digest2.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(len(merges2), 1)

    def test_dissimilar_entries_are_never_proposed_for_merge(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("z", body="A completely unrelated statement about spreadsheets.")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(merges, [])

    def test_a_three_way_near_duplicate_cluster_promotes_instead_of_merging(self) -> None:
        # A hub matched by TWO other entries (cluster size 3) is "a real
        # content match across multiple inbox entries" -- promote, not merge.
        self._write_inbox("hub", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("dup1", body="The quick brown fox jumps over the lazy dog today!")
        self._write_inbox("dup2", body="The quick brown fox jumps over the lazy dog today?")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        stages = [p.stage for p in digest.proposals]
        self.assertIn(it.PROMOTE_STAGE, stages)
        self.assertNotIn(it.MERGE_STAGE, stages)
        promote = next(p for p in digest.proposals if p.stage == it.PROMOTE_STAGE)
        self.assertEqual(len(promote.paths), 3)

    def test_merge_mutation_matches_dream_stage_dedups_own_shape(self) -> None:
        """Byte-level proof of reuse, not just behavioral similarity: run
        dream._stage_dedup directly against the same fixture and assert
        inbox_triage's merge mutation is identical (modulo the restaged
        `stage` label)."""
        import dream

        a = self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        b = self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        entries, loaded = it._still_untriaged(self.vault)

        direct = dream._stage_dedup(entries, loaded)
        self.assertEqual(len(direct), 1)

        # Gate open (confident verdict) so the merge disposition
        # materializes -- this test's intent is the mutation REUSE, not
        # the verdict gate.
        with _ConfidentMergeVerdict():
            digest = it.run_inbox_triage(self.vault, now=time.time())
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0].mutations, direct[0].mutations)
        self.assertEqual(merges[0].kind, direct[0].kind)


# -----------------------------------------------------------------------------
# Pre-existing-backlog-shaped promote/merge candidates auto-apply too
# (2026-07-11, second pass) -- the operator's ruling is explicit that this
# is NOT limited to expire: "moving forward no need to confirm gate the
# inbox" retires the gate for every disposition, on every entry, backlog
# era included.
# -----------------------------------------------------------------------------

class BacklogPromoteAndMergeAutoApplyTests(_InboxTriageTestBase):
    def test_backlog_shaped_promote_candidate_auto_applies_without_confirmation(self) -> None:
        # No `created` field -- the real legacy-backlog shape.
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
        )
        self.assertEqual(len(digest.proposals), 1)
        self.assertEqual(digest.proposals[0].stage, it.PROMOTE_STAGE)
        self.assertEqual(len(batch.items), 1, "a backlog-shaped promote candidate must auto-apply, no confirm call")

        canonical = self.vault / "personal" / "workflow" / "h.md"
        self.assertTrue(canonical.exists(), "promote must have applied with zero operator action")
        self.assertEqual(self._status(self._inbox_dir() / "h.md"), "promoted")

    def test_backlog_shaped_merge_pair_auto_applies_without_confirmation(self) -> None:
        # No `created` field on either side of the pair.
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        with _ConfidentMergeVerdict():
            digest, batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
            )
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(len(merges), 1)
        self.assertEqual(len(batch.items), 1, "a backlog-shaped merge pair must auto-apply, no confirm call")
        self.assertEqual(batch.items[0]["stage"], it.MERGE_STAGE)
        self.assertEqual(self._status(self._inbox_dir() / "b.md"), "superseded")


# -----------------------------------------------------------------------------
# The 255-survivor scenario: an entry left at `status: inbox` by a PRIOR
# run (the "kept" side of an earlier merge, never itself given a
# disposition) must be triaged and auto-applied on a LATER run, not stuck
# forever the way the retired confirm-gate would have left it.
# -----------------------------------------------------------------------------

class SurvivorRetriageTests(_InboxTriageTestBase):
    def test_a_previously_kept_merge_survivor_gets_triaged_and_auto_applied_on_a_later_run(self) -> None:
        # Simulate the 255-survivor shape from the real first supervised
        # pass: an entry with no `created` field (pre-existing-backlog
        # shaped) that was never itself resolved by an earlier run --
        # exactly like the survivor side of a confirmed merge cluster,
        # still sitting at `status: inbox`.
        self._write_inbox("survivor", body="A hunch that survived an earlier merge round, untouched since.")

        now0 = time.time()
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now0, revert_log=self.revert_log, lock_root=self.lock_root,
        )
        # Alone in the pool, unreinforced, no `created` field -- it's
        # proposed for expire under the backlog era label.
        self.assertEqual(len(digest.proposals), 1)
        self.assertEqual(digest.proposals[0].stage, it.BACKLOG_EXPIRE_STAGE)
        self.assertEqual(
            len(batch.items), 1,
            "the survivor must be triaged and auto-applied on this run, not left stuck forever",
        )
        self.assertEqual(self._status(self._inbox_dir() / "survivor.md"), "expired")

        # A SECOND survivor written after the first run resolved -- proves
        # this isn't a one-shot fluke; every subsequent run keeps clearing
        # the backlog the same way.
        self._write_inbox("survivor-2", body="A second hunch, same shape, arriving later.")
        digest2, batch2 = it.run_inbox_triage_and_auto_apply(
            self.vault, run_id="second-run", now=now0 + 1, revert_log=self.revert_log, lock_root=self.lock_root,
        )
        self.assertEqual(len(digest2.proposals), 1)
        self.assertEqual(len(batch2.items), 1)
        self.assertEqual(self._status(self._inbox_dir() / "survivor-2.md"), "expired")


# -----------------------------------------------------------------------------
# The review CLI's staging/confirm/revert/reject flow round-trips
# -----------------------------------------------------------------------------

class ReviewFlowTests(_InboxTriageTestBase):
    def test_confirm_applies_through_revert_log_and_reverts_cleanly(self) -> None:
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        digest = it.run_inbox_triage(self.vault, now=time.time())

        stdin = _TTYStringIO("c\n")
        stats = it.review_inbox_triage(self.vault, digest.run_id, self.revert_log, stdin=stdin, stdout=io.StringIO())
        self.assertEqual(stats, {"total": 1, "confirmed": 1, "rejected": 0, "skipped": 0, "errors": 0})

        canonical = self.vault / "personal" / "workflow" / "h.md"
        self.assertTrue(canonical.exists())

        state = json.loads((self.vault / "_dream-staging" / digest.run_id / "state.json").read_text(encoding="utf-8"))
        entry_id = state["1"]["entry_id"]
        self.revert_log.revert(digest.run_id, entry_id)

        self.assertFalse(canonical.exists(), "revert must undo the canonical write")
        self.assertEqual(self._status(self._inbox_dir() / "h.md"), "inbox", "revert must restore the source entry's original status")

    def test_reject_marks_triage_rejected_and_a_fresh_scan_never_re_proposes_it(self) -> None:
        self._write_inbox("z", body="An unreinforced hunch, far past any TTL.")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        self.assertEqual(len(digest.proposals), 1)

        stdin = _TTYStringIO("r\n")
        stats = it.review_inbox_triage(self.vault, digest.run_id, self.revert_log, stdin=stdin, stdout=io.StringIO())
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(self._status(self._inbox_dir() / "z.md"), "triage_rejected")

        digest2 = it.run_inbox_triage(self.vault, run_id="second-run", now=time.time())
        self.assertEqual(digest2.proposals, [], "a rejected entry must never be re-proposed by a later scan")

    def test_non_tty_stdin_defaults_every_prompt_to_skip(self) -> None:
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        digest = it.run_inbox_triage(self.vault, now=time.time())

        stats = it.review_inbox_triage(
            self.vault, digest.run_id, self.revert_log,
            stdin=io.StringIO("c\n"), stdout=io.StringIO(), stderr=io.StringIO(),
        )
        self.assertEqual(stats["confirmed"], 0)
        self.assertEqual(stats["skipped"], 1)

    def test_a_confirmed_overlapping_proposal_makes_a_sibling_proposal_moot(self) -> None:
        # h is claimed by BOTH a promote (occurrence-based) proposal from
        # run 1... simulate this by manually staging two runs that both
        # reference the same file, the way two separate scans a few days
        # apart legitimately could before either is confirmed.
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        digest1 = it.run_inbox_triage(self.vault, run_id="run-1", now=time.time())
        stdin = _TTYStringIO("c\n")
        it.review_inbox_triage(self.vault, digest1.run_id, self.revert_log, stdin=stdin, stdout=io.StringIO())
        self.assertEqual(self._status(self._inbox_dir() / "h.md"), "promoted")

        # A second, stale manifest referencing the same (now-resolved) path
        # -- review must skip it without prompting or erroring.
        stale_proposal = it.Proposal(
            stage=it.PROMOTE_STAGE, kind="promote",
            paths=[str(self._inbox_dir() / "h.md")], summary="stale duplicate proposal",
            mutations=[(self._inbox_dir() / "h.md", "should never apply")],
        )
        stale_digest = it.InboxTriageDigest(
            run_id="run-2-stale", cutover_at=digest1.cutover_at,
            corpus_stats={"entry_count": 1, "total_bytes": 0}, proposals=[stale_proposal],
        )
        it._stage_digest_and_staging(self.vault, stale_digest, staged_at=time.time())

        stdin2 = _TTYStringIO("c\n")
        stats2 = it.review_inbox_triage(self.vault, "run-2-stale", self.revert_log, stdin=stdin2, stdout=io.StringIO())
        self.assertEqual(stats2["total"], 0, "a proposal whose path is no longer status: inbox must never be re-offered")


# -----------------------------------------------------------------------------
# `--no-auto-apply` still proposes without applying anything -- the
# explicit opt-out for someone who wants to inspect proposals before they
# apply, still correct against the new auto-apply-by-default contract.
# -----------------------------------------------------------------------------

class NoAutoApplyStillProposesOnlyTests(_InboxTriageTestBase):
    def test_no_auto_apply_flag_proposes_without_applying_any_disposition(self) -> None:
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        self._write_inbox("z", body="An unreinforced hunch, far past any TTL.")

        rc = it.main([
            "--vault-path", str(self.vault), "--no-auto-apply", "--non-interactive",
            "--log-root", str(self.log_root), "--lock-root", str(self.lock_root),
        ])
        self.assertEqual(rc, 0)

        run_id = it._most_recent_run_id(self.vault)
        self.assertIsNotNone(run_id)
        pending = dc.list_pending(self.vault, run_id)
        self.assertTrue(pending)
        for p in pending:
            self.assertEqual(p.status, "pending", "--no-auto-apply must leave every proposal pending")

        # Nothing applied -- neither the promote nor the expire mutation.
        self.assertFalse((self.vault / "personal" / "workflow" / "h.md").exists())
        self.assertEqual(self._status(self._inbox_dir() / "h.md"), "inbox")
        self.assertEqual(self._status(self._inbox_dir() / "z.md"), "inbox")

        digest_text = (self.vault / "_dream-staging" / run_id / "digest.md").read_text(encoding="utf-8")
        self.assertNotIn("AUTO-APPLIED", digest_text)


# -----------------------------------------------------------------------------
# The manual `--list` / `--confirm` / `--reject` CLI paths -- kept working
# on purpose (per the operator's brief: someone might still want to
# inspect or intervene on a specific proposal by hand later).
# -----------------------------------------------------------------------------

class ManualCliPathsTests(_InboxTriageTestBase):
    def test_list_confirm_reject_round_trip_via_the_cli(self) -> None:
        self._write_inbox("h", kind="workflow", occurrences=4, body="Always run tests before pushing.")
        self._write_inbox("z", body="An unreinforced hunch, far past any TTL.")

        # Scan only (--no-auto-apply) so both proposals are still pending
        # for the manual paths to act on.
        rc = it.main([
            "--vault-path", str(self.vault), "--no-auto-apply", "--non-interactive",
            "--log-root", str(self.log_root), "--lock-root", str(self.lock_root),
        ])
        self.assertEqual(rc, 0)
        run_id = it._most_recent_run_id(self.vault)
        self.assertIsNotNone(run_id)

        # --list: scriptable JSON dump of pending proposals, no prompts.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = it.main(["--vault-path", str(self.vault), "--list", "--run-id", run_id])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload), 2)
        self.assertTrue(all(p["status"] == "pending" for p in payload))

        # --confirm one proposal directly.
        confirm_entry = next(p for p in payload if p["stage"] == it.PROMOTE_STAGE)
        buf_confirm = io.StringIO()
        with redirect_stdout(buf_confirm):
            rc = it.main([
                "--vault-path", str(self.vault), "--confirm", str(confirm_entry["index"]), "--run-id", run_id,
                "--log-root", str(self.log_root), "--lock-root", str(self.lock_root),
            ])
        self.assertEqual(rc, 0)
        confirm_result = json.loads(buf_confirm.getvalue())
        self.assertEqual(confirm_result["action"], "confirmed")
        self.assertTrue((self.vault / "personal" / "workflow" / "h.md").exists())

        # --reject the other proposal directly.
        reject_entry = next(p for p in payload if p["stage"] != it.PROMOTE_STAGE)
        buf_reject = io.StringIO()
        with redirect_stdout(buf_reject):
            rc = it.main([
                "--vault-path", str(self.vault), "--reject", str(reject_entry["index"]), "--run-id", run_id,
                "--log-root", str(self.log_root), "--lock-root", str(self.lock_root),
            ])
        self.assertEqual(rc, 0)
        reject_result = json.loads(buf_reject.getvalue())
        self.assertEqual(reject_result["action"], "rejected")
        self.assertEqual(self._status(self._inbox_dir() / "z.md"), "triage_rejected")

        # A subsequent --list against the same run reflects both resolutions.
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc = it.main(["--vault-path", str(self.vault), "--list", "--run-id", run_id])
        self.assertEqual(rc, 0)
        payload2 = json.loads(buf2.getvalue())
        statuses = {p["index"]: p["status"] for p in payload2}
        self.assertEqual(statuses[confirm_entry["index"]], "confirmed")
        # The rejected proposal's own manifest entry status is unaffected
        # by --reject (reject is a direct frontmatter patch outside
        # dream_confirm's state machine) -- what changed is the SOURCE
        # entry's frontmatter, asserted above.

    def test_confirm_and_reject_require_run_id(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf):
            rc = it.main(["--vault-path", str(self.vault), "--confirm", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("--confirm requires --run-id", buf.getvalue())

        buf2 = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf2):
            rc = it.main(["--vault-path", str(self.vault), "--reject", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("--reject requires --run-id", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()


# -----------------------------------------------------------------------------
# Cluster-aware dedup (auto-org part 3, task 3): fingerprint-exact families
# collapse deterministically; fuzzy merges are verdict-gated; ambiguous
# pairs land on needs-your-eye.
# -----------------------------------------------------------------------------

class ClusterAwareDedupTests(_InboxTriageTestBase):
    def test_exact_family_of_four_collapses_to_one(self) -> None:
        # Formatting variants of one body -- identical after normalize_body,
        # however deep the suffix family goes.
        base = "The same insight captured four times."
        self._write_inbox("insight", created="2026-07-01T00:00:00+00:00", body=base)
        self._write_inbox("insight-1", created="2026-07-02T00:00:00+00:00", body=f"  {base}  ")
        self._write_inbox("insight-2", created="2026-07-03T00:00:00+00:00", body=base.upper())
        self._write_inbox("insight-3", created="2026-07-04T00:00:00+00:00", body=f"{base}\n\n")

        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
        )
        collapses = [p for p in digest.proposals if p.stage == it.COLLAPSE_STAGE]
        self.assertEqual(len(collapses), 1)
        self.assertEqual(len(collapses[0].paths), 4)
        # Canonical survivor = the earliest by `created`.
        self.assertEqual(Path(collapses[0].paths[0]).name, "insight.md")
        # Auto-applied without a confirm call, one disposition for the family.
        self.assertIn(it.COLLAPSE_STAGE, {item["stage"] for item in batch.items})
        # Copies: superseded, marked -- and STILL PRESENT ON DISK.
        for copy_name in ("insight-1.md", "insight-2.md", "insight-3.md"):
            copy = self._inbox_dir() / copy_name
            self.assertTrue(copy.exists(), f"{copy_name} must be marked, never deleted")
            self.assertEqual(self._status(copy), "superseded")
            self.assertIn("supersedes:", copy.read_text(encoding="utf-8"))
        # Survivor: untouched content, still status inbox (its own
        # disposition comes on a later cycle like any live candidate).
        survivor = self._inbox_dir() / "insight.md"
        self.assertEqual(self._status(survivor), "inbox")
        self.assertIn(base, survivor.read_text(encoding="utf-8"))

    def test_superseded_copies_enter_the_tidying_lanes_shape(self) -> None:
        # The collapse marks copies `status: superseded` -- the same status
        # dream's own dedup mutation writes, which part 1's tidying lanes
        # already treat as a normal aged-entry input. Assert the exact
        # frontmatter shape those lanes key on.
        base = "A duplicated observation."
        self._write_inbox("obs", created="2026-07-01T00:00:00+00:00", body=base)
        self._write_inbox("obs-1", created="2026-07-02T00:00:00+00:00", body=f"{base} ")
        with _ConfidentMergeVerdict():  # gate irrelevant for exact families; belt-and-suspenders
            digest, _batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
            )
        copy_raw = (self._inbox_dir() / "obs-1.md").read_text(encoding="utf-8")
        self.assertIn("status: superseded", copy_raw)
        self.assertIn(f"supersedes: {self._inbox_dir() / 'obs.md'}", copy_raw)

    def test_fuzzy_pair_confident_yes_verdict_collapses(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        with _ConfidentMergeVerdict():
            digest, batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
            )
        self.assertEqual(len([p for p in digest.proposals if p.stage == it.MERGE_STAGE]), 1)
        self.assertEqual(digest.needs_your_eye, [])
        self.assertEqual(self._status(self._inbox_dir() / "b.md"), "superseded")

    def test_fuzzy_pair_unsure_verdict_lands_on_needs_your_eye(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        with mock.patch.object(it.dream, "cheap_model_tier_available", return_value=True), \
             mock.patch.object(it, "judge_fuzzy_merge", return_value="unsure"):
            digest, batch = it.run_inbox_triage_and_auto_apply(
                self.vault, now=time.time(), revert_log=self.revert_log, lock_root=self.lock_root,
            )
        self.assertEqual([p for p in digest.proposals if p.stage == it.MERGE_STAGE], [])
        self.assertEqual(len(digest.needs_your_eye), 1)
        self.assertIn("unsure", digest.needs_your_eye[0]["reason"])
        # Both notes untouched, still inbox.
        self.assertEqual(self._status(self._inbox_dir() / "a.md"), "inbox")
        self.assertEqual(self._status(self._inbox_dir() / "b.md"), "inbox")

    def test_fuzzy_pair_confident_no_verdict_keeps_both(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        with mock.patch.object(it.dream, "cheap_model_tier_available", return_value=True), \
             mock.patch.object(it, "judge_fuzzy_merge", return_value="no"):
            digest = it.run_inbox_triage(self.vault, now=time.time())
        self.assertEqual([p for p in digest.proposals if p.stage == it.MERGE_STAGE], [])
        self.assertEqual(digest.needs_your_eye, [])

    def test_tier_unavailable_routes_to_needs_your_eye_no_judge_call(self) -> None:
        # The plan's fail-closed rule with a call-count check: the judge is
        # NEVER invoked when the tier is unavailable.
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        with mock.patch.object(it, "judge_fuzzy_merge") as judge_spy:
            digest = it.run_inbox_triage(self.vault, now=time.time())
        judge_spy.assert_not_called()
        self.assertEqual(len(digest.needs_your_eye), 1)

    def test_needs_your_eye_pair_exempt_from_same_run_expiry(self) -> None:
        # An ambiguous pair past the TTL must NOT expire in the very run
        # that flagged it.
        now0 = time.time()
        old = "2026-01-01T00:00:00+00:00"
        self._write_inbox("a", created=old, body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", created=old, body="The quick brown fox jumps over the lazy dog today!")
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now0, revert_log=self.revert_log, lock_root=self.lock_root,
        )
        self.assertEqual(len(digest.needs_your_eye), 1)
        self.assertEqual(self._status(self._inbox_dir() / "a.md"), "inbox")
        self.assertEqual(self._status(self._inbox_dir() / "b.md"), "inbox")

    def test_needs_your_eye_state_file_written_and_cleared(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        it.run_inbox_triage(self.vault, now=time.time())
        state_path = it._needs_your_eye_path(self.vault)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["items"]), 1)

        # Operator resolves it (edits one note apart) -- next cycle's
        # recompute clears the list.
        b = self._inbox_dir() / "b.md"
        b.write_text(
            b.read_text(encoding="utf-8").replace(
                "The quick brown fox jumps over the lazy dog today!",
                "A completely rewritten, unrelated thought.",
            ),
            encoding="utf-8",
        )
        it.run_inbox_triage(self.vault, now=time.time())
        payload2 = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload2["items"], [])


# -----------------------------------------------------------------------------
# Inbox triage folds into the weekly dreaming cycle (auto-org part 3, task 4)
# -----------------------------------------------------------------------------

class TriageFoldsIntoDreamingTests(_InboxTriageTestBase):
    def test_weekly_dreaming_cycle_processes_inbox_without_explicit_invocation(self) -> None:
        import dream

        # An exact suffix family in the inbox -- nothing else invokes
        # /memory inbox; the weekly cycle alone must collapse it.
        base = "One insight, captured twice."
        self._write_inbox("dup", created="2026-07-01T00:00:00+00:00", body=base)
        self._write_inbox("dup-1", created="2026-07-02T00:00:00+00:00", body=f"  {base}")

        digest, _batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="weekly-1", revert_log=self.revert_log, lock_root=self.lock_root,
        )

        self.assertIsNotNone(digest.inbox_triage_run)
        self.assertGreaterEqual(digest.inbox_triage_run["auto_applied"], 1)
        self.assertEqual(self._status(self._inbox_dir() / "dup-1.md"), "superseded")
        self.assertEqual(self._status(self._inbox_dir() / "dup.md"), "inbox")
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Inbox triage (folded into this cycle)", digest_text)

    def test_on_demand_cli_path_produces_same_disposition_on_same_fixture(self) -> None:
        import dream

        base = "One insight, captured twice."

        # Fixture A: processed by the weekly dreaming cycle.
        self._write_inbox("dup", created="2026-07-01T00:00:00+00:00", body=base)
        self._write_inbox("dup-1", created="2026-07-02T00:00:00+00:00", body=f"  {base}")
        dream.run_dream_and_auto_apply(
            self.vault, run_id="weekly-2", revert_log=self.revert_log, lock_root=self.lock_root,
        )
        weekly_statuses = {
            "dup": self._status(self._inbox_dir() / "dup.md"),
            "dup-1": self._status(self._inbox_dir() / "dup-1.md"),
        }

        # Fixture B (a second, identical family in a FRESH vault): the
        # standalone on-demand engine, same dispositions.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp2:
            vault2 = Path(tmp2) / "vault"
            (vault2 / "personal" / "_inbox").mkdir(parents=True)
            for slug, created, body in (
                ("dup", "2026-07-01T00:00:00+00:00", base),
                ("dup-1", "2026-07-02T00:00:00+00:00", f"  {base}"),
            ):
                (vault2 / "personal" / "_inbox" / f"{slug}.md").write_text(
                    _INBOX_TEMPLATE.format(
                        kind="idea", created_line=f"created: {created}\n", slug=slug,
                        confidence="LOW", occurrences=1, body=body,
                    ),
                    encoding="utf-8",
                )
            log2 = Path(tmp2) / "revert-log"
            lock2 = Path(tmp2) / "locks"
            from revert_log import RevertLog
            it.run_inbox_triage_and_auto_apply(
                vault2, now=time.time(),
                revert_log=RevertLog(vault2, log_root=log2, lock_root=lock2),
                lock_root=lock2,
            )
            standalone_statuses = {
                "dup": it._current_status(vault2 / "personal" / "_inbox" / "dup.md"),
                "dup-1": it._current_status(vault2 / "personal" / "_inbox" / "dup-1.md"),
            }

        self.assertEqual(weekly_statuses, standalone_statuses)

    def test_bare_run_dream_stays_propose_only_no_triage(self) -> None:
        import dream

        base = "One insight, captured twice."
        self._write_inbox("dup", body=base)
        self._write_inbox("dup-1", body=f"  {base}")
        digest = dream.run_dream(self.vault, run_id="bare-1")
        self.assertIsNone(digest.inbox_triage_run)
        # Nothing applied -- both candidates untouched.
        self.assertEqual(self._status(self._inbox_dir() / "dup.md"), "inbox")
        self.assertEqual(self._status(self._inbox_dir() / "dup-1.md"), "inbox")

    def test_fold_can_be_disabled_for_isolation(self) -> None:
        import dream

        self._write_inbox("solo", body="A single candidate.")
        digest, _batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="weekly-3", revert_log=self.revert_log, lock_root=self.lock_root,
            include_inbox_triage=False,
        )
        self.assertIsNone(digest.inbox_triage_run)
