"""The part-5 stages the nightly pass still runs: the breaker's status, the
backlink footers and the correction loop.

Each is exercised against a fake ledger rather than a live daemon, because what
is being tested is what the stage decides — what it writes, and what it leaves
alone — and not whether a subprocess starts. Entity rollups, stub synthesis and
the unfiled drain retired in agentm-vault plan 04; the last test here holds
that a pass over a ledger full of their old work enqueues nothing.
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


class ReportingTests(StageTestCase):
    def test_every_stage_reports_itself(self):
        self.install(FakeLedger(pending={"eligible": 0, "current": 0, "pending": []}))
        results = dream_stages.run_new_stages(Path("/nonexistent"))
        names = [r.stage for r in results]
        # `breaker` joined the list when part 6 task 3 added it. It reports every
        # cycle rather than only when it is open, so it belongs here with the
        # rest — a stage that only appeared on the bad nights would leave the
        # reader unable to tell "auto-apply is running" from "nobody checked".
        # `correction` joined it at task 4, and runs last: it reads what the
        # corpus currently looks like, so it should see the state this cycle
        # leaves rather than the state it started from.
        self.assertEqual(names, ["breaker", "correction"])
        for r in results:
            self.assertIn("stage", r.as_dict())

    def test_correction_runs_after_the_footers(self):
        # Stated as an ordering, because the reason it is last is substantive:
        # correction measures the corpus, and a footer changes a note.
        self.install(FakeLedger(pending={"eligible": 0, "current": 0, "pending": []}))
        names = [r.stage for r in dream_stages.run_new_stages(
            Path("/nonexistent"), footer_targets=["memory/semantic/a.md"])]
        self.assertEqual(names, ["breaker", "backlink_footers", "correction"])

    def test_a_ledger_full_of_the_retired_stages_work_enqueues_nothing(self):
        # Plan 04: entity rollups, stub synthesis and the unfiled drain
        # retired. Each would have enqueued something from this ledger — an
        # entity with no file, a target three notes expect, an unfiled note
        # owed a pass. The pass must enqueue none of it.
        fake = self.install(FakeLedger(
            entities=[{"name": "commit:a73ff0f4f5dc", "mentions": 9}],
            dangling=[{"target": "stop", "sources": ["a.md", "b.md", "c.md"]}],
            pending={"eligible": 4, "current": 0,
                     "pending": [{"path": "memory/semantic/x.md"}]},
        ))
        dream_stages.run_new_stages(Path("/nonexistent"), enrich_enabled=True)
        self.assertEqual(fake.enqueued, [])

    def test_the_correction_stage_is_wired_in_and_not_merely_callable(self):
        # The hole task 3 paid for: a test that calls a stage directly stays
        # green when the caller stops calling it. This one goes through
        # `run_new_stages`, which is the only path the nightly pass takes.
        self.install(FakeLedger(pending={"eligible": 0, "current": 0, "pending": []}))
        called = {}
        original = dream_stages.stage_correction

        def spy(vault_path, **kw):
            called.update(kw)
            called["vault_path"] = vault_path
            return original(vault_path, **kw)

        dream_stages.stage_correction = spy
        self.addCleanup(lambda: setattr(dream_stages, "stage_correction",
                                        original))
        dream_stages.run_new_stages(Path("/nonexistent"), enrich_enabled=True,
                                    run_id="r-1", version="v9")
        self.assertTrue(called, "run_new_stages never reached stage_correction")

    def test_the_stage_is_handed_what_the_caller_gave_run_new_stages(self):
        # A stage wired in but handed nothing would report "deferred" every
        # night, forever, and look exactly like a stage working correctly with
        # enrichment off.
        self.install(FakeLedger(pending={"eligible": 0, "current": 0, "pending": []}))
        seen = {}
        original = dream_stages.stage_correction

        def spy(vault_path, **kw):
            seen.update(kw)
            return original(vault_path, **kw)

        dream_stages.stage_correction = spy
        self.addCleanup(lambda: setattr(dream_stages, "stage_correction",
                                        original))
        sentinel = object()
        dream_stages.run_new_stages(Path("/nonexistent"), enrich_enabled=True,
                                    run_id="r-2", version="v9",
                                    revert_log=sentinel, distiller=sentinel)
        self.assertEqual(seen.get("run_id"), "r-2")
        self.assertEqual(seen.get("version"), "v9")
        self.assertIs(seen.get("revert_log"), sentinel)
        self.assertIs(seen.get("distiller"), sentinel)
        self.assertTrue(seen.get("enrich_enabled"))


if __name__ == "__main__":
    unittest.main()
