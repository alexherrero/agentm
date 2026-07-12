#!/usr/bin/env python3
"""Unit tests for `inbox_triage.py` — the `_inbox/` bulk-review pass
(`/memory inbox --bulk-review`).

`inbox_triage.py` lives in `harness/skills/memory/scripts/` (same cross-dir
import pattern as `test_dream.py` / `test_dream_confirm.py` /
`test_watchlist_review.py`).

Covers the operator's own 2026-07-11 verification list:
  - the cutover-marker logic correctly classifies pre-existing vs.
    post-cutover entries (`CutoverMarkerTests`)
  - the first pass over a fixture backlog never auto-applies anything,
    regardless of disposition (`FirstPassNeverAutoAppliesTests`)
  - a post-cutover entry's expire proposal DOES auto-apply after its TTL,
    while promote/merge proposals on OTHER post-cutover entries do NOT
    (`PostCutoverAutoApplyTests`)
  - promote reuses the real curated-destination convention
    (`save._build_frontmatter` / `save.save_entry()`'s path+frontmatter
    shape) (`PromoteReusesCanonicalConventionTests`)
  - merge reuses the real dedup-similarity logic (`dream._stage_dedup`,
    same threshold, same merge shape) (`MergeReusesDedupLogicTests`)
  - the CLI's staging/confirm/revert flow round-trips correctly, and the
    reject path stops a re-proposal on the next scan (`ReviewFlowTests`)
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
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
# First pass over the backlog never auto-applies anything
# -----------------------------------------------------------------------------

class FirstPassNeverAutoAppliesTests(_InboxTriageTestBase):
    def test_first_pass_stages_every_disposition_but_auto_applies_none(self) -> None:
        # A merge pair, an occurrence-reinforced promote candidate, and a
        # stale unreinforced entry -- one of each disposition, all with no
        # `created` field (the real legacy-backlog shape).
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        self._write_inbox("c", kind="preferences", occurrences=5, body="Always use tabs not spaces.")
        self._write_inbox("d", body="A completely unrelated single hunch.")

        now0 = time.time()
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now0, revert_log=self.revert_log, lock_root=self.lock_root,
        )

        self.assertGreaterEqual(len(digest.proposals), 3)
        self.assertEqual(batch.items, [], "the very first run must never auto-apply anything")

        pending = dc.list_pending(self.vault, digest.run_id)
        self.assertTrue(pending, "every proposal must exist")
        for p in pending:
            self.assertEqual(p.status, "pending", f"proposal #{p.index} ({p.stage}) must stay pending on the first pass")

        # Every proposal this run staged under the backlog-only stage, or
        # a stage that never auto-applies regardless of age.
        stages = {p.stage for p in digest.proposals}
        self.assertNotIn(it.AUTO_APPLY_ELIGIBLE_STAGE, stages, "no entry here has a created field, so nothing is post-cutover")


# -----------------------------------------------------------------------------
# Post-cutover: expire auto-applies, promote/merge on other entries do not
# -----------------------------------------------------------------------------

class PostCutoverAutoApplyTests(_InboxTriageTestBase):
    def test_expire_auto_applies_while_sibling_promote_and_merge_stay_pending(self) -> None:
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
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now1, revert_log=self.revert_log, lock_root=self.lock_root,
        )

        by_stage = {p.stage for p in digest.proposals}
        self.assertIn(it.AUTO_APPLY_ELIGIBLE_STAGE, by_stage)
        self.assertIn(it.MERGE_STAGE, by_stage)
        self.assertIn(it.PROMOTE_STAGE, by_stage)

        # Exactly the expire proposal auto-applied.
        applied_stages = {item["stage"] for item in batch.items}
        self.assertEqual(applied_stages, {it.AUTO_APPLY_ELIGIBLE_STAGE})
        self.assertEqual(self._status(self._inbox_dir() / "stale.md"), "expired")

        # Merge + promote proposals on the OTHER post-cutover entries stay pending.
        pending_by_stage = {p.stage: p.status for p in dc.list_pending(self.vault, digest.run_id)}
        self.assertEqual(pending_by_stage[it.MERGE_STAGE], "pending")
        self.assertEqual(pending_by_stage[it.PROMOTE_STAGE], "pending")
        self.assertEqual(self._status(self._inbox_dir() / "dup1.md"), "inbox")
        self.assertEqual(self._status(self._inbox_dir() / "reinforced.md"), "inbox")

    def test_backlog_expire_never_auto_applies_even_past_ttl(self) -> None:
        now0 = time.time()
        it.ensure_cutover_marker(self.vault, now=now0)
        self._write_inbox("old", body="No created field at all -- pre-existing backlog.")

        now1 = now0 + (365 * 86400)
        digest, batch = it.run_inbox_triage_and_auto_apply(
            self.vault, now=now1, revert_log=self.revert_log, lock_root=self.lock_root,
        )
        self.assertEqual(batch.items, [])
        stages = {p.stage for p in digest.proposals}
        self.assertIn(it.BACKLOG_EXPIRE_STAGE, stages)
        self.assertNotIn(it.AUTO_APPLY_ELIGIBLE_STAGE, stages)


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
    def test_near_duplicate_pair_proposes_merge_at_dream_similarity_threshold(self) -> None:
        self._write_inbox("a", body="The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", body="The quick brown fox jumps over the lazy dog today!")
        digest = it.run_inbox_triage(self.vault, now=time.time())
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(len(merges), 1)
        self.assertEqual(set(Path(p).name for p in merges[0].paths), {"a.md", "b.md"})

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

        digest = it.run_inbox_triage(self.vault, now=time.time())
        merges = [p for p in digest.proposals if p.stage == it.MERGE_STAGE]
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0].mutations, direct[0].mutations)
        self.assertEqual(merges[0].kind, direct[0].kind)


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


if __name__ == "__main__":
    unittest.main()
