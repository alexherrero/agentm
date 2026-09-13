#!/usr/bin/env python3
"""The maps gates (agentm-vault plan 07). Each fails on a fixture that breaks its
rule once the maps data run's marker exists, reports without failing before it,
and passes a fixture that keeps the shape."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_REPO / "scripts"), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402
import maps_shape as ms  # noqa: E402
import storage_rules  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classes = _load("check-class-directories")

RULES = storage_rules.StorageRules({
    "memory_types": ["convention", "fix", "idea", "workflow"],
    "record_kinds": ["moc", "dir-index", "session-trace"],
    "thresholds": {"moc_min_members": 5},
    "deprecations": {"tip": "idea"},
})

MAP = "---\ntitle: a map\nkind: moc\nstatus: active\n---\n\n# a map\n"


def card(t: str = "", *, lifecycle: str = "active", status: str = "active", kind: str = "") -> str:
    head = f"kind: {kind}" if kind else f"type: {t}"
    return f"---\ntitle: a note\n{head}\nstatus: {status}\nlifecycle: {lifecycle}\ncreated: 2026-09-01\n---\n\nA body.\n"


class _Memory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for c in ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs"):
            (self.root / "memory" / c).mkdir(parents=True)
        # The card backfill has run, so the card half of the gate enforces too.
        (self.root / "memory" / cs.MARKER_NAME).write_text("run r\n", encoding="utf-8")

    def write(self, rel: str, text: str) -> Path:
        p = self.root / "memory" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def members(self, t: str, n: int, cls: str = "semantic"):
        for i in range(n):
            self.write(f"{cls}/{t}-note-{i}.md", card(t))

    def data_run_done(self):
        ms.marker_path(self.root).write_text("run m\n", encoding="utf-8")


class LiveTypeCounts(_Memory):
    def test_counts_what_the_mocs_job_counts(self):
        self.members("idea", 3)
        self.write("procedural/w.md", card("workflow"))
        self.write("semantic/old.md", card("idea", lifecycle="superseded"))
        self.write("semantic/kept.md", card("idea", lifecycle="archived"))
        self.write("semantic/replaced.md", card("idea", status="superseded"))
        self.write("episodic/trace.md", card(kind="session-trace"))
        self.write("semantic/_index.md", "---\nkind: dir-index\nstatus: active\n---\n")
        self.write("semantic/retired.md", card("tip"))
        self.write("mocs/idea.md", "---\ntitle: a map\ntype: idea\n---\n")
        self.assertEqual(ms.live_type_counts(self.root, RULES), {"idea": 4, "workflow": 1},
                         "a record, the class index, a settled note or the maps' own class was counted, "
                         "or a retired type was not counted under its replacement")

    def test_the_threshold_is_the_contracts(self):
        self.assertEqual(ms.min_members(RULES), 5)
        self.assertEqual(ms.min_members(storage_rules.StorageRules({"thresholds": {"moc_min_members": 3}})), 3)
        self.assertEqual(ms.min_members(None), ms.DEFAULT_MIN_MEMBERS)


class MocsShape(_Memory):
    def gate(self):
        buf = io.StringIO()
        code = classes.check(self.root, (set(RULES.memory_types()), set(RULES.record_kinds())),
                             out=buf, rules=RULES)
        return code, buf.getvalue()

    def shaped(self):
        self.members("convention", 5)
        self.members("fix", 4)
        for name in ("moc-root", "moc-memory", "needs-review", "convention"):
            self.write(f"mocs/{name}.md", MAP)
        self.write("mocs/_index.md", "---\ntitle: mocs\nkind: dir-index\nstatus: active\n---\n")

    def test_the_three_maps_a_type_page_and_the_class_index_pass(self):
        self.shaped()
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertEqual(ms.mocs_findings(self.root, RULES), [])

    def test_a_numbered_page_fails_once_the_data_run_is_done(self):
        self.shaped()
        self.write("mocs/convention-2.md", MAP)
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("memory/mocs/convention-2.md: a numbered map page", out)

    def test_a_page_under_the_threshold_fails(self):
        self.shaped()
        self.write("mocs/fix.md", MAP)
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("`fix` has 4 live note(s), under the page threshold of 5", out)

    def test_a_note_that_is_no_map_fails(self):
        self.shaped()
        self.write("mocs/Home.md", MAP)
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("memory/mocs/Home.md: not a map this directory holds", out)

    def test_before_the_data_run_it_reports_and_passes(self):
        self.shaped()
        self.write("mocs/convention-2.md", MAP)
        self.write("mocs/fix.md", MAP)
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("before the maps data run — 2 map finding(s)", out)
        self.assertIn("pending: memory/mocs/convention-2.md", out)

    def test_a_lower_contract_threshold_gives_a_small_type_its_page(self):
        self.shaped()
        self.write("mocs/fix.md", MAP)
        low = storage_rules.StorageRules({"memory_types": ["convention", "fix"],
                                          "thresholds": {"moc_min_members": 4}})
        self.assertEqual(ms.mocs_findings(self.root, low), [])

    def test_a_card_finding_still_fails_beside_a_clean_mocs(self):
        # The maps branch is added to the card gate, not substituted for it.
        self.shaped()
        self.data_run_done()
        self.write("semantic/both.md", "---\ntype: idea\nkind: moc\nstatus: active\n---\n")
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("carries both `type` and `kind`", out)


roots = _load("check-root-notes")

INDEX = ("# Vault\n\n| space | what it holds | authority |\n|---|---|---|\n"
         "| `Agent/` | memory | FRIDAY writes freely |\n\nFRIDAY's own entry point is [[moc-root]].\n")


class RootNotes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "Agent"
        (self.root / "memory" / "mocs").mkdir(parents=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def retired_shape(self):
        self.write("index.md", INDEX)
        self.write("Agent/memory/mocs/moc-root.md", "# root\n")
        self.write("Projects/index.md", "Lands as one revertible commit. See [[../index|Filing]].\n")
        self.write("Projects/agentm/decisions/scan.md", "| 6 | our wiki + [[moc-root\\|Home]] MOC | core |\n")
        self.write("Agent/memory/semantic/blog-author.md", "- [[moc-root|Home]] — vault map.\n")

    def data_run_done(self):
        ms.marker_path(self.root).write_text("run m\n", encoding="utf-8")

    def gate(self):
        buf = io.StringIO()
        return roots.check(self.root, out=buf), buf.getvalue()

    def test_the_retired_shape_passes(self):
        self.retired_shape()
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_the_retired_notes_on_disk_fail(self):
        self.retired_shape()
        self.write("Agent/Home.md", "# Home\n")
        self.write("Filing.md", "# Filing\n\n| space | authority |\n|---|---|\n")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("Agent/Home.md: still exists", out)
        self.assertIn("Filing.md: still exists", out)

    def test_a_link_left_to_either_fails_by_its_text(self):
        self.retired_shape()
        self.write("Projects/agentm/pattern/funnel.md",
                   "- [[Home]] — vault map.\n- see [[Filing]]\n- [[Agent/Home]]\n- [[home|the map]]\n"
                   "- [the table](../../../Filing.md)\n")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        for line, link in ((1, "[[Home]]"), (2, "[[Filing]]"), (3, "[[Agent/Home]]"), (4, "[[home|the map]]")):
            self.assertIn(f"Projects/agentm/pattern/funnel.md:{line}: {link}", out)
        self.assertIn("Projects/agentm/pattern/funnel.md:5:", out)

    def test_code_a_record_and_your_own_space_are_not_findings(self):
        self.retired_shape()
        self.write("Agent/memory/episodic/trace.md",
                   "- **The `[[Home]]` trap:** a bare link would open the personal note.\n\n"
                   "```\n[[Filing]]\n```\n")
        self.write("Projects/agentm/_harness/progress-x.md", "Links [[Home]] and [[Filing]] as written then.\n")
        self.write("Personal/Home/To Do, Lists, Specs/list.md", "Back to [[Home]].\n")
        self.write("Projects/other.md", "My list: [[Personal/Home/To Do, Lists, Specs/Home]].\n")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_the_authority_table_appears_once(self):
        self.retired_shape()
        self.write("index.md", INDEX + "\n| space | authority |\n|---|---|\n| `Calendar/` | shared |\n")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("index.md: carries the write-authority table 2 time(s), not once", out)
        self.write("index.md", "# Vault\n\nNo table here.\n")
        _code, out = self.gate()
        self.assertIn("index.md: carries the write-authority table 0 time(s), not once", out)

    def test_before_the_data_run_it_reports_and_passes(self):
        self.retired_shape()
        self.write("Agent/Home.md", "# Home\n")
        self.write("Projects/p.md", "[[Home]]\n")
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("before the maps data run — 2 finding(s)", out)
        self.assertIn("pending: Projects/p.md:1: [[Home]] names a retired note", out)


calroot = _load("check-calendar-root")


class CalendarRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "Agent"
        (self.root / "memory").mkdir(parents=True)
        (self.vault / "Calendar").mkdir()

    def write(self, rel: str, text: str = "x\n") -> Path:
        p = self.vault / "Calendar" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def data_run_done(self):
        ms.marker_path(self.root).write_text("run m\n", encoding="utf-8")

    def gate(self):
        buf = io.StringIO()
        return calroot.check(self.root, out=buf), buf.getvalue()

    def test_years_their_maps_and_the_daily_note_allowances_pass(self):
        self.write("2026/2026-08-10-diary.md")
        self.write("moc-calendar-2026.md")
        self.write("_daily-template.md")
        self.write("2026-09-13.md")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("note — Calendar/_daily-template.md: allowed at the root until plan 10", out)
        self.assertIn("note — Calendar/2026-09-13.md: allowed at the root until plan 10", out)
        self.assertIn("clean", out)

    def test_a_stray_note_or_folder_at_the_root_fails(self):
        self.write("2026/2026-08-10-diary.md")
        self.write("moc-calendar-2026.md")
        self.write("notes.md")
        self.write("drafts/idea.md")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("Calendar/notes.md: the calendar root holds years and their maps", out)
        self.assertIn("Calendar/drafts/: not a year directory", out)

    def test_a_year_map_with_no_year_fails(self):
        self.write("moc-calendar-2025.md")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 1, out)
        self.assertIn("Calendar/moc-calendar-2025.md: a year map with no 2025/ beside it", out)

    def test_a_year_still_waiting_for_its_map_is_a_note_not_a_failure(self):
        self.write("2027/2027-01-01-diary.md")
        self.data_run_done()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("note — Calendar/2027/: no moc-calendar-2027.md yet; the night writes it", out)

    def test_before_the_data_run_it_reports_and_passes(self):
        self.write("notes.md")
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("before the maps data run — 1 finding(s)", out)
        self.assertIn("pending: Calendar/notes.md", out)

    def test_no_calendar_is_nothing_to_check(self):
        (self.vault / "Calendar").rmdir()
        code, out = self.gate()
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
