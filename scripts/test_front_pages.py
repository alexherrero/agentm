#!/usr/bin/env python3
"""Front pages that have fallen behind the work under them (task 205).

A scratch vault with projects and systems whose dates are set by hand, held
to the rule: a charter, a project tracker or a system overview is flagged
when it is more than a week older than the newest record beneath it, and the
dates a migration, a map or the enrichment pass leaves never count as work.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import corpus_scorecard as sc  # noqa: E402
import front_pages as fp  # noqa: E402

TODAY = date(2026, 11, 13)


def note(path: Path, body: str = "Body.\n", **fields) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "".join(f"{k}: {v}\n" for k, v in fields.items())
    path.write_text(f"---\n{head}---\n\n{body}", encoding="utf-8")
    return path


class _Vault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / "agent").mkdir()

    def stale(self, **kw):
        return fp.stale_front_pages(self.vault, today=TODAY, **kw)

    def pages(self, **kw):
        return [s.page for s in self.stale(**kw)[0]]


class CharterTests(_Vault):
    def test_a_charter_behind_a_task_record_by_more_than_a_week_is_flagged(self):
        note(self.vault / "projects/house/charter.md", kind="project-index", created="2026-05-01",
             updated="2026-10-20")
        note(self.vault / "projects/house/candidates/one.md", kind="reference", created="2026-11-01")
        note(self.vault / "projects/house/research/two.md", kind="research", created="2026-10-01",
             updated="2026-11-10")
        note(self.vault / "projects/house/decisions/old.md", kind="decision", created="2026-06-01")
        stale, checked = self.stale()
        self.assertEqual(checked, 1)
        self.assertEqual(len(stale), 1)
        row = stale[0]
        self.assertEqual((row.page, row.page_date), ("projects/house/charter.md", "2026-10-20"))
        self.assertEqual((row.newest, row.newest_date), ("projects/house/research/two.md", "2026-11-10"))
        self.assertEqual(row.days_behind, 21)
        self.assertEqual(row.newer, 2, "two records are newer than the charter, the old decision is not")

    def test_a_charter_within_the_week_is_not(self):
        note(self.vault / "projects/house/charter.md", kind="project-index", updated="2026-11-03")
        note(self.vault / "projects/house/candidates/one.md", kind="reference", created="2026-11-10")
        self.assertEqual(self.stale(), ([], 1))
        # One day past the line is flagged; the line is a named constant.
        note(self.vault / "projects/house/candidates/two.md", kind="reference", created="2026-11-11")
        self.assertEqual(self.pages(), ["projects/house/charter.md"])
        self.assertEqual(fp.GRACE_DAYS, 7)
        self.assertEqual(self.pages(grace=30), [])

    def test_what_the_night_and_the_migrations_write_is_not_work(self):
        note(self.vault / "projects/p/charter.md", kind="project-index", updated="2026-09-01")
        # The night's map, the generated project tracker, an enrichment-only
        # date, an unedited blueprint and a date in the future.
        note(self.vault / "projects/p/moc-p.md", kind="moc", created="2026-11-12", updated="2026-11-12")
        note(self.vault / "projects/p/tracker.md", kind="tracker", opened="2026-04-01", updated="2026-11-12")
        note(self.vault / "projects/p/decisions/d.md", kind="decision", created="2026-08-01",
             updated="2026-11-11", enriched_at='"2026-11-11T10:27:36Z"')
        note(self.vault / "projects/p/blueprint.md", kind="blueprint", created="2026-11-09", updated="2026-11-09")
        note(self.vault / "projects/p/research/later.md", kind="research", created="2026-12-25")
        note(self.vault / "projects/p/.harness/hidden.md", kind="reference", created="2026-11-12")
        # Two front pages were checked, the charter and the tracker; neither is behind.
        self.assertEqual(self.stale(), ([], 2))
        # A blueprint someone edited after it was generated is work.
        note(self.vault / "projects/p/blueprint.md", kind="blueprint", created="2026-09-25", updated="2026-11-09")
        row = self.stale()[0][0]
        self.assertEqual((row.newest, row.newest_date), ("projects/p/blueprint.md", "2026-11-09"))

    def test_a_charter_with_no_date_or_nothing_beneath_it_is_silent(self):
        note(self.vault / "projects/empty/charter.md", kind="project-index", updated="2026-01-01")
        (self.vault / "projects/undated").mkdir(parents=True)
        (self.vault / "projects/undated/charter.md").write_text("# no header\n", encoding="utf-8")
        note(self.vault / "projects/undated/x.md", kind="reference", created="2026-11-10")
        # The empty project was checked and is fine; the undated one cannot be.
        self.assertEqual(self.stale(), ([], 1))

    def test_finished_projects_are_not_walked_as_one_project(self):
        note(self.vault / "projects/completed/charter.md", kind="project-index", updated="2026-01-01")
        note(self.vault / "projects/completed/old-one/notes.md", kind="reference", created="2026-11-10")
        self.assertEqual(self.stale(), ([], 0))

    def test_a_date_that_is_not_a_date_is_ignored_and_a_file_time_never_counts(self):
        note(self.vault / "projects/p/charter.md", kind="project-index", updated="2026-11-01")
        rec = note(self.vault / "projects/p/research/r.md", kind="research", created="2026-13-45",
                   updated="soon")
        os.utime(rec, None)  # written just now, which says nothing
        self.assertEqual(self.stale(), ([], 1))


class TrackerAndSystemTests(_Vault):
    def test_a_project_tracker_is_held_to_its_task_records_only(self):
        note(self.vault / "projects/p/tracker.md", kind="tracker", opened="2026-04-01", updated="2026-10-01")
        # A decision is the charter's business, not the tracker's.
        note(self.vault / "projects/p/decisions/d.md", kind="decision", created="2026-11-10")
        self.assertEqual(self.stale(), ([], 1))
        note(self.vault / "projects/p/tasks/007-x/tracker.md", kind="tracker", opened="2026-10-01",
             updated="2026-10-02", closed="2026-11-09")
        row = self.stale()[0][0]
        self.assertEqual((row.page, row.newest, row.newest_date, row.days_behind),
                         ("projects/p/tracker.md", "projects/p/tasks/007-x/tracker.md", "2026-11-09", 39))

    def test_a_finished_tasks_record_counts_for_the_tracker_too(self):
        note(self.vault / "projects/p/tracker.md", kind="tracker", opened="2026-04-01", updated="2026-10-01")
        note(self.vault / "projects/p/completed/tasks/003-y/progress.md", kind="progress-log",
             created="2026-09-01", updated="2026-10-20")
        self.assertEqual(self.pages(), ["projects/p/tracker.md"])

    def test_a_system_overview_behind_its_component_is_flagged(self):
        note(self.vault / "systems/homelab/system.md", kind="system", created="2026-09-25", updated="2026-10-01")
        note(self.vault / "systems/homelab/components/nas.md", kind="component", created="2026-09-25",
             updated="2026-11-12")
        note(self.vault / "systems/mac/system.md", kind="system", created="2026-09-25", updated="2026-11-06")
        note(self.vault / "systems/moc-systems.md", kind="moc", updated="2026-11-13")
        stale, checked = self.stale()
        self.assertEqual(checked, 2)
        self.assertEqual([(s.page, s.days_behind) for s in stale], [("systems/homelab/system.md", 42)])

    def test_the_most_behind_page_comes_first(self):
        for name, updated in (("a", "2026-10-25"), ("b", "2026-09-01"), ("c", "2026-10-10")):
            note(self.vault / f"projects/{name}/charter.md", kind="project-index", updated=updated)
            note(self.vault / f"projects/{name}/docs/x.md", kind="reference", created="2026-11-12")
        self.assertEqual(self.pages(), ["projects/b/charter.md", "projects/c/charter.md", "projects/a/charter.md"])


class ScorecardSectionTests(_Vault):
    def section(self):
        return sc.section_front_pages(self.vault / "agent", today=TODAY)

    def test_the_section_names_each_stale_page_and_what_overtook_it(self):
        note(self.vault / "projects/house/charter.md", kind="project-index", updated="2026-10-20")
        note(self.vault / "projects/house/candidates/one.md", kind="reference", created="2026-11-10")
        note(self.vault / "systems/mac/system.md", kind="system", updated="2026-11-06")
        text = self.section().render()
        self.assertIn("| front pages checked | 2 |", text)
        self.assertIn("| more than 7 days behind | 1 | STALE: listed below, most behind first |", text)
        self.assertIn("| `projects/house/charter.md` | 21 days behind | last updated 2026-10-20; "
                      "1 record(s) beneath it are newer, the newest 2026-11-10: "
                      "`projects/house/candidates/one.md` |", text)

    def test_the_warning_clears_when_the_page_is_brought_up_to_date(self):
        charter = self.vault / "projects/house/charter.md"
        note(charter, kind="project-index", updated="2026-10-20")
        note(self.vault / "projects/house/candidates/one.md", kind="reference", created="2026-11-10")
        self.assertIn("STALE", self.section().render())
        note(charter, kind="project-index", updated="2026-11-12")
        text = self.section().render()
        self.assertNotIn("STALE", text)
        self.assertIn("| more than 7 days behind | 0 | every front page is within a week of the work under it |", text)

    def test_a_vault_with_no_front_pages_says_so_rather_than_zero(self):
        text = self.section().render()
        self.assertIn("| front pages checked | — | not measured: no project or system front page carries a date |", text)
        self.assertNotIn("| more than 7 days behind |", text)

    def test_a_long_list_is_capped_and_the_rest_counted(self):
        for i in range(sc.FRONT_PAGE_ROWS + 3):
            note(self.vault / f"projects/p{i:02d}/charter.md", kind="project-index", updated="2026-09-01")
            note(self.vault / f"projects/p{i:02d}/docs/x.md", kind="reference", created="2026-11-10")
        section = self.section()
        listed = [r for r in section.readings if r.label.startswith("`projects/")]
        self.assertEqual(len(listed), sc.FRONT_PAGE_ROWS)
        self.assertIn("| and more | 3 |", section.render())

    def test_a_flat_vault_is_its_own_root(self):
        flat = Path(self._tmp.name) / "flat"
        note(flat / "projects/p/charter.md", kind="project-index", updated="2026-10-01")
        note(flat / "projects/p/docs/x.md", kind="reference", created="2026-11-10")
        self.assertEqual(sc.vault_root_of(flat), flat)
        self.assertEqual(sc.vault_root_of(self.vault / "agent"), self.vault)
        self.assertIn("`projects/p/charter.md`", sc.section_front_pages(flat, today=TODAY).render())


if __name__ == "__main__":
    unittest.main()
