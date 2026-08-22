"""The calendar layer, consolidation, and task-to-project promotion.

The three properties the plan names, each tested against the thing that would go
wrong: a trace rewritten by consolidation, a root document changed without
alignment, and a workbench dragged across instead of preserved.
"""

import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                      / "harness" / "skills" / "memory" / "scripts"))

import calendar_layer  # noqa: E402
import promote  # noqa: E402


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.vault, True)

    def write(self, rel, body):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path


class CalendarTests(VaultTestCase):
    def test_a_trace_lands_where_the_design_says(self):
        rel = calendar_layer.trace_path(date(2026, 8, 22), "the median decision")
        self.assertEqual(rel, "calendar/2026/2026-08-22_the-median-decision.md")

    def test_a_trace_records_what_was_touched(self):
        trace = calendar_layer.Trace(
            when=date(2026, 8, 22), slug="a session",
            summary="Settled the latency statistic.",
            touched=["memory/b.md", "memory/a.md"],
            entities=["repo:alexherrero/agentm"],
        )
        rel = calendar_layer.write_trace(self.vault, trace)
        body = (self.vault / rel).read_text(encoding="utf-8")

        # "What happened" without "to what" is a diary entry.
        self.assertIn("[[memory/a.md]]", body)
        self.assertIn("[[memory/b.md]]", body)
        # Sorted, so an unchanged day renders identically.
        self.assertLess(body.index("memory/a.md"), body.index("memory/b.md"))
        # And addressable from the entity side.
        self.assertIn("repo:alexherrero/agentm", body)

    def test_a_second_session_the_same_day_does_not_delete_the_first(self):
        """A session is not a day. Two sessions on one afternoon are two things
        that happened."""
        first = calendar_layer.Trace(
            when=date(2026, 8, 22), slug="s", summary="The morning.")
        rel = calendar_layer.write_trace(self.vault, first)
        second = calendar_layer.Trace(
            when=date(2026, 8, 22), slug="s", summary="The afternoon.")
        again = calendar_layer.write_trace(self.vault, second)

        self.assertEqual(rel, again)
        body = (self.vault / rel).read_text(encoding="utf-8")
        self.assertIn("The morning.", body)
        self.assertIn("The afternoon.", body)
        # One frontmatter block, not two — a second would make the note parse as
        # prose containing YAML.
        self.assertEqual(body.count("\ntype: reference"), 1)

    def test_a_day_with_an_unusable_title_still_gets_a_trace(self):
        """Losing the record because the title was punctuation is the opposite
        of what an episodic layer is for."""
        rel = calendar_layer.trace_path(date(2026, 8, 22), "!!! ???")
        self.assertEqual(rel, "calendar/2026/2026-08-22_session.md")


class ConsolidationTests(VaultTestCase):
    def make_days(self):
        rels = []
        for day in (20, 21, 22):
            trace = calendar_layer.Trace(
                when=date(2026, 8, day), slug="s",
                summary=f"What happened on the {day}th.")
            rels.append(calendar_layer.write_trace(self.vault, trace))
        return rels

    def test_the_trace_is_never_rewritten(self):
        """The property the plan names, checked the only way it can be: hash
        every trace before and after."""
        rels = self.make_days()
        before = calendar_layer.digest_traces(self.vault, rels)
        self.assertEqual(len(before), 3)

        card = calendar_layer.consolidate(self.vault, calendar_layer.Consolidation(
            slug="latency bars measure the machine",
            lesson="Read an overhead comparison off a median, not a maximum.",
            sources=rels,
        ))

        after = calendar_layer.digest_traces(self.vault, rels)
        self.assertEqual(before, after,
                         "consolidation changed a trace; the derived claim would "
                         "survive and its evidence would not")
        self.assertTrue((self.vault / card).exists())

    def test_the_card_says_what_it_was_built_from(self):
        rels = self.make_days()
        card = calendar_layer.consolidate(self.vault, calendar_layer.Consolidation(
            slug="a lesson", lesson="Something learned.", sources=rels))
        body = (self.vault / card).read_text(encoding="utf-8")

        self.assertIn(calendar_layer.CONSOLIDATED_FROM, body)
        for rel in rels:
            self.assertIn(rel, body)

    def test_a_card_with_no_sources_is_refused(self):
        """A lesson that cannot say what it was built from is an assertion
        wearing the shape of a conclusion."""
        with self.assertRaises(ValueError):
            calendar_layer.consolidate(self.vault, calendar_layer.Consolidation(
                slug="a lesson", lesson="Something.", sources=[]))

    def test_a_card_with_no_lesson_is_refused(self):
        with self.assertRaises(ValueError):
            calendar_layer.consolidate(self.vault, calendar_layer.Consolidation(
                slug="a lesson", lesson="   ", sources=["calendar/2026/a.md"]))

    def test_the_card_lands_in_the_crystallized_class(self):
        rels = self.make_days()
        card = calendar_layer.consolidate(self.vault, calendar_layer.Consolidation(
            slug="a lesson", lesson="Something.", sources=rels))
        self.assertTrue(card.startswith("memory/crystallized/"), card)


class PromotionTests(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.write("desk/tasks/an-investigation/progress.md",
                   "The false starts and the notes to self.\n")

    def test_promotion_preserves_the_workbench(self):
        """The property the plan names. The workbench is the record of how the
        thinking went, and moving it loses the execution log."""
        before = (self.vault / "desk/tasks/an-investigation/progress.md").read_bytes()

        res = promote.promote(self.vault, promote.Promotion(
            task="an-investigation", project="a-real-project",
            documents={"README.md": "The project's visible face.\n"},
        ))

        after = (self.vault / "desk/tasks/an-investigation/progress.md").read_bytes()
        self.assertEqual(before, after, "the workbench was rewritten")
        self.assertEqual(res.preserved, "desk/tasks/an-investigation")
        self.assertTrue((self.vault / "desk/projects/a-real-project/README.md").exists())

    def test_the_workbench_says_where_the_work_went(self):
        promote.promote(self.vault, promote.Promotion(
            task="an-investigation", project="a-real-project"))
        marker = self.vault / "desk/tasks/an-investigation" / promote.PROMOTED_MARKER
        self.assertTrue(marker.exists(), "the old workbench is a dead end")
        self.assertIn("a-real-project", marker.read_text(encoding="utf-8"))

    def test_the_project_documents_are_authored_fresh(self):
        """Not dragged across. A project whose face is somebody's scratch paper
        is what moving produces."""
        res = promote.promote(self.vault, promote.Promotion(
            task="an-investigation", project="a-real-project",
            documents={"README.md": "Written for this project.\n"},
        ))
        body = (self.vault / "desk/projects/a-real-project/README.md").read_text(
            encoding="utf-8")
        self.assertEqual(body, "Written for this project.\n")
        self.assertIn("desk/projects/a-real-project/README.md", res.written)

    def test_promotion_refuses_to_replace_a_root_document(self):
        """Replacing a document at a project's root is exactly what the door
        requires alignment for, and an operation named for creating something
        must not hide it."""
        self.write("desk/projects/a-real-project/README.md", "The operator's own.\n")
        with self.assertRaises(ValueError):
            promote.promote(self.vault, promote.Promotion(
                task="an-investigation", project="a-real-project",
                documents={"README.md": "Something else.\n"},
            ))
        self.assertEqual(
            (self.vault / "desk/projects/a-real-project/README.md").read_text(
                encoding="utf-8"),
            "The operator's own.\n")

    def test_promoting_a_task_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            promote.promote(self.vault, promote.Promotion(
                task="never-existed", project="a-real-project"))

    def test_a_second_promotion_does_not_rewrite_the_marker(self):
        """Rewriting it identically is not free: every write lands in the vault's
        git history, and a marker rewritten on every run puts a diff in the log
        that says nothing."""
        first = promote.promote(self.vault, promote.Promotion(
            task="an-investigation", project="a-real-project"))
        marker_rel = f"desk/tasks/an-investigation/{promote.PROMOTED_MARKER}"
        self.assertIn(marker_rel, first.written)

        again = promote.promote(self.vault, promote.Promotion(
            task="an-investigation", project="a-real-project"))
        self.assertNotIn(marker_rel, again.written,
                         "the marker was written a second time")

        body = (self.vault / marker_rel).read_text(encoding="utf-8")
        self.assertEqual(body.count("became a project"), 1)

    def test_a_promotion_needs_both_slugs(self):
        """An empty slug resolves to the tasks root itself, which exists — so
        without this guard a promotion with no task would look like a promotion
        of every task at once."""
        for task, project in (("", "a-real-project"),
                              ("an-investigation", ""),
                              ("", "")):
            with self.assertRaises(ValueError):
                promote.promote(self.vault, promote.Promotion(
                    task=task, project=project))


if __name__ == "__main__":
    unittest.main()
