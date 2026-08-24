"""The four stages part 5 adds to the nightly pass.

Every one of them is exercised against a fake ledger rather than a live daemon,
because what is being tested is what the stage decides — which gaps it finds,
what it enqueues, and what it writes — and not whether a subprocess starts.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                      / "harness" / "skills" / "memory" / "scripts"))

import dream_stages  # noqa: E402
import work_ledger  # noqa: E402


class FakeLedger:
    """Stands in for the daemon, recording what was enqueued."""

    def __init__(self, *, entities=None, dangling=None, backlinks=None,
                 pending=None, fail=False):
        self._entities = entities or []
        self._dangling = dangling or []
        self._backlinks = backlinks or {}
        self._pending = pending or {}
        self._fail = fail
        self.enqueued = []

    def _check(self):
        if self._fail:
            raise work_ledger.LedgerUnavailable("the daemon is not on PATH")

    def entity_mentions(self, min_mentions=1, limit=0):
        self._check()
        return [e for e in self._entities if e.get("mentions", 0) >= min_mentions]

    def dangling_targets(self, min_sources=1, limit=0):
        self._check()
        return [d for d in self._dangling
                if len(d.get("sources") or []) >= min_sources]

    def backlinks(self, rel):
        self._check()
        return self._backlinks.get(rel, [])

    def pending(self, stage):
        self._check()
        return self._pending

    def enqueue(self, owner, target, reason):
        self._check()
        self.enqueued.append((owner, target, reason))


class StageTestCase(unittest.TestCase):
    def install(self, fake):
        """Point the stages at `fake` for the duration of one test."""
        for name in ("entity_mentions", "dangling_targets", "backlinks",
                     "pending", "enqueue"):
            original = getattr(work_ledger, name)
            setattr(work_ledger, name, getattr(fake, name))
            self.addCleanup(setattr, work_ledger, name, original)
        return fake


class EntityRollupTests(StageTestCase):
    def test_an_entity_with_no_file_is_enqueued(self):
        fake = self.install(FakeLedger(entities=[
            {"uri": "person:ada-lovelace", "mentions": 40},
            {"uri": "repo:agentm", "mentions": 12, "file": "memory/entities/agentm.md"},
        ]))
        res = dream_stages.stage_entity_rollups()

        self.assertEqual(res.considered, 2)
        self.assertEqual(res.enqueued, 1)
        self.assertEqual(res.skipped, 1)
        owner, target, reason = fake.enqueued[0]
        self.assertEqual(owner, dream_stages.OWNER_ROLLUP)
        self.assertEqual(target, "person:ada-lovelace")
        # The reason carries the number, because "needs a rollup" tells whoever
        # reads the queue nothing about whether it is worth draining.
        self.assertIn("40", reason)

    def test_an_entity_that_already_has_a_file_is_not_enqueued(self):
        fake = self.install(FakeLedger(entities=[
            {"uri": "repo:agentm", "mentions": 99, "file": "memory/entities/agentm.md"},
        ]))
        dream_stages.stage_entity_rollups()
        self.assertEqual(fake.enqueued, [])

    def test_the_threshold_is_applied(self):
        """Below the floor a rollup would mostly restate its own title."""
        fake = self.install(FakeLedger(entities=[
            {"uri": "person:mentioned-once", "mentions": 1},
            {"uri": "person:mentioned-often", "mentions": 40},
        ]))
        dream_stages.stage_entity_rollups()
        targets = [t for _, t, _ in fake.enqueued]
        self.assertEqual(targets, ["person:mentioned-often"])

    def test_an_unavailable_daemon_is_reported_rather_than_guessed(self):
        """A cycle that ran without the ledger did not do the work badly — it
        did not do the work, and the digest should say which."""
        self.install(FakeLedger(fail=True))
        res = dream_stages.stage_entity_rollups()
        self.assertTrue(res.unavailable)
        self.assertEqual(res.enqueued, 0)


class StubSynthesisTests(StageTestCase):
    def test_a_target_several_notes_expect_is_enqueued(self):
        fake = self.install(FakeLedger(dangling=[
            {"target": "the-median-decision",
             "sources": ["a.md", "b.md", "c.md"]},
        ]))
        res = dream_stages.stage_stub_synthesis()

        self.assertEqual(res.enqueued, 1)
        owner, target, reason = fake.enqueued[0]
        self.assertEqual(owner, dream_stages.OWNER_STUB)
        self.assertEqual(target, "the-median-decision")
        self.assertIn("3", reason)

    def test_a_single_source_target_is_left_alone(self):
        """One note linking to something that does not exist is as likely a typo
        as a gap, and a stub for a typo resolves the link and hides the
        mistake."""
        fake = self.install(FakeLedger(dangling=[
            {"target": "probably-a-typo", "sources": ["a.md"]},
        ]))
        dream_stages.stage_stub_synthesis()
        self.assertEqual(fake.enqueued, [])


class FooterTests(StageTestCase):
    def test_a_footer_is_written_below_the_marker(self):
        fake = self.install(FakeLedger(backlinks={
            "memory/target.md": [{"resolved": "memory/b.md"},
                                 {"resolved": "memory/a.md"}],
        }))
        vault = Path(self.mkvault({"memory/target.md": "---\ntitle: T\n---\n\nthe body\n"}))
        written = {}
        res = dream_stages.stage_backlink_footers(
            vault, ["memory/target.md"],
            write=lambda p, t: written.__setitem__(str(p), t))

        self.assertEqual(res.written, 1)
        body = next(iter(written.values()))
        self.assertIn(dream_stages.FOOTER_BEGIN, body)
        self.assertIn(dream_stages.FOOTER_END, body)
        # Sorted, so an unchanged corpus rewrites nothing.
        self.assertLess(body.index("memory/a.md"), body.index("memory/b.md"))
        # And the operator's own text is untouched, byte for byte.
        self.assertTrue(body.startswith("---\ntitle: T\n---\n\nthe body\n"))
        self.assertEqual(fake.enqueued, [])

    def test_a_second_pass_replaces_rather_than_stacks(self):
        """A note linked to for a year would otherwise carry a year of
        footers."""
        first = dream_stages.apply_footer(
            "body\n", dream_stages.render_footer(["a.md"]))
        second = dream_stages.apply_footer(
            first, dream_stages.render_footer(["a.md", "b.md"]))

        self.assertEqual(second.count(dream_stages.FOOTER_BEGIN), 1)
        self.assertEqual(second.count(dream_stages.FOOTER_END), 1)
        self.assertIn("b.md", second)

    def test_the_footer_is_exactly_removable(self):
        """The revert, and what makes writing at all safe: what the pass wrote
        comes off leaving what the operator wrote byte-identical."""
        original = "---\ntitle: T\n---\n\nthe body somebody typed\n"
        with_footer = dream_stages.apply_footer(
            original, dream_stages.render_footer(["a.md", "b.md"]))
        self.assertNotEqual(with_footer, original)
        self.assertEqual(dream_stages.strip_footer(with_footer), original)

    def test_a_footer_that_is_not_at_the_end_is_still_removed_exactly(self):
        """Reachable, not hypothetical: `apply_footer` replaces a block in
        place, so once a human has moved one the pass keeps writing it where it
        sits. At the end of a file a stray newline is absorbed by the trailing
        normalisation; in the middle of one it is a blank line that accumulates
        on every cycle."""
        before = "---\ntitle: T\n---\n\nthe first half\n\n"
        after = "\nthe second half, below where the footer ended up\n"
        moved = before + dream_stages.render_footer(["a.md"]) + after

        stripped = dream_stages.strip_footer(moved)
        self.assertEqual(stripped, before + after)

    def test_a_note_nothing_points_at_loses_its_footer(self):
        """An empty footer claims the question was asked and answered nothing."""
        self.install(FakeLedger(backlinks={"memory/target.md": []}))
        body = dream_stages.apply_footer("the body\n",
                                         dream_stages.render_footer(["gone.md"]))
        vault = Path(self.mkvault({"memory/target.md": body}))
        written = {}
        res = dream_stages.stage_backlink_footers(
            vault, ["memory/target.md"],
            write=lambda p, t: written.__setitem__(str(p), t))

        self.assertEqual(res.written, 1)
        self.assertNotIn(dream_stages.FOOTER_BEGIN, next(iter(written.values())))

    def test_an_unchanged_footer_is_not_rewritten(self):
        """Every write lands in the vault's git history, so a pass over an
        unchanged corpus has to write nothing."""
        self.install(FakeLedger(backlinks={
            "memory/target.md": [{"resolved": "memory/a.md"}],
        }))
        body = dream_stages.apply_footer(
            "the body\n", dream_stages.render_footer(["memory/a.md"]))
        vault = Path(self.mkvault({"memory/target.md": body}))
        written = {}
        res = dream_stages.stage_backlink_footers(
            vault, ["memory/target.md"],
            write=lambda p, t: written.__setitem__(str(p), t))

        self.assertEqual(res.written, 0)
        self.assertEqual(res.skipped, 1)
        self.assertEqual(written, {})

    def test_a_note_in_the_index_and_not_on_disk_is_skipped(self):
        """A drifted index is the reconcile pass's job; failing the run over one
        missing file would make drift look like a broken pass."""
        self.install(FakeLedger(backlinks={"memory/gone.md": [{"resolved": "a.md"}]}))
        vault = Path(self.mkvault({}))
        res = dream_stages.stage_backlink_footers(vault, ["memory/gone.md"])
        self.assertEqual(res.skipped, 1)
        self.assertEqual(res.written, 0)

    def test_a_note_does_not_reference_itself(self):
        self.install(FakeLedger(backlinks={
            "memory/target.md": [{"resolved": "memory/target.md"},
                                 {"resolved": "memory/a.md"}],
        }))
        vault = Path(self.mkvault({"memory/target.md": "body\n"}))
        written = {}
        dream_stages.stage_backlink_footers(
            vault, ["memory/target.md"],
            write=lambda p, t: written.__setitem__(str(p), t))
        body = next(iter(written.values()))
        self.assertNotIn("[[memory/target.md]]", body)

    def mkvault(self, files):
        import tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        for rel, body in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root


class UnfiledDrainTests(StageTestCase):
    def test_it_reports_coverage_without_acting_when_off(self):
        """Part 4 deferred the drain over the standing queue. Nothing here
        changes that; the stage reports the number and spends nothing."""
        fake = self.install(FakeLedger(pending={
            "eligible": 8765, "current": 25,
            "pending": [{"target": "memory/a.md", "reason": "never"}],
        }))
        res = dream_stages.stage_unfiled_drain(enabled=False)

        self.assertEqual(res.considered, 1)
        self.assertEqual(res.enqueued, 0)
        self.assertEqual(fake.enqueued, [])
        self.assertTrue(any("8765" in n for n in res.notes))
        self.assertTrue(any("off" in n for n in res.notes))

    def test_it_enqueues_when_turned_on(self):
        fake = self.install(FakeLedger(pending={
            "eligible": 3, "current": 1,
            "pending": [{"target": "memory/a.md", "reason": "stale"},
                        {"target": "memory/b.md", "reason": "never"}],
        }))
        res = dream_stages.stage_unfiled_drain(enabled=True)

        self.assertEqual(res.enqueued, 2)
        self.assertEqual([t for _, t, _ in fake.enqueued],
                         ["memory/a.md", "memory/b.md"])
        # The reason travels with it, so the owner knows why it is being asked.
        self.assertIn("stale", fake.enqueued[0][2])

    def test_a_budget_bounds_what_one_cycle_enqueues(self):
        fake = self.install(FakeLedger(pending={
            "eligible": 100, "current": 0,
            "pending": [{"target": f"memory/{i}.md", "reason": "never"}
                        for i in range(50)],
        }))
        dream_stages.stage_unfiled_drain(enabled=True, budget=5)
        self.assertEqual(len(fake.enqueued), 5)


class ReportingTests(StageTestCase):
    def test_every_stage_reports_itself(self):
        self.install(FakeLedger(pending={"eligible": 0, "current": 0, "pending": []}))
        results = dream_stages.run_new_stages(Path("/nonexistent"))
        names = [r.stage for r in results]
        # `breaker` joined the list when part 6 task 3 added it. It reports every
        # cycle rather than only when it is open, so it belongs here with the
        # rest — a stage that only appeared on the bad nights would leave the
        # reader unable to tell "auto-apply is running" from "nobody checked".
        self.assertEqual(names,
                         ["breaker", "entity_rollups", "stub_synthesis",
                          "unfiled_drain"])
        for r in results:
            self.assertIn("stage", r.as_dict())


if __name__ == "__main__":
    unittest.main()
