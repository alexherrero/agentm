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


if __name__ == "__main__":
    unittest.main()
