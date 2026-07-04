#!/usr/bin/env python3
"""Tests for scripts/health/designed_vs_built.py (R2.6 / agTrack#3).

A synthetic mini design corpus (delivered / [PENDING-IMPL] / status:launched-
but-unbuilt) asserts the classifier gets all three cases right — the
fixture the plan's Task 1 Verification calls for.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import designed_vs_built as dvb  # noqa: E402


def _write_design(root: Path, name: str, *, status: str = "launched",
                   governs: list[str] | None = None, body: str = "") -> Path:
    designs = root / "wiki" / "designs"
    designs.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {name}", f"status: {status}", "kind: design", "scope: feature"]
    if governs is not None:
        if governs:
            lines.append("governs:")
            for g in governs:
                lines.append(f"  - {g}")
        else:
            lines.append("governs: []")
    lines += ["---", "", f"# {name}", body]
    p = designs / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestFrontmatterParsing(unittest.TestCase):
    def test_bracket_list_governs(self) -> None:
        text = "---\nstatus: launched\nkind: design\ngoverns: [a.py, b.py]\n---\nbody\n"
        fm = dvb._parse_frontmatter(text)
        self.assertEqual(fm["governs"], ["a.py", "b.py"])

    def test_block_list_governs(self) -> None:
        text = "---\nstatus: launched\nkind: design\ngoverns:\n  - a.py\n  - b.py\n---\nbody\n"
        fm = dvb._parse_frontmatter(text)
        self.assertEqual(fm["governs"], ["a.py", "b.py"])

    def test_empty_bracket_list(self) -> None:
        text = "---\nstatus: launched\ngoverns: []\n---\nbody\n"
        fm = dvb._parse_frontmatter(text)
        self.assertEqual(fm["governs"], [])


class TestClassifyDesignThreeCases(unittest.TestCase):
    """The plan's required regression fixture: delivered / PENDING-IMPL / launched-but-unbuilt."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_delivered_case_classifies_built(self) -> None:
        # governed target exists on disk, no PENDING-IMPL marker.
        (self.root / "scripts" / "shipped.py").write_text("# shipped\n", encoding="utf-8")
        path = _write_design(
            self.root, "delivered.md", status="launched", governs=["scripts/shipped.py"],
            body="Fully delivered capability, nothing pending.",
        )
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["classification"], "built")

    def test_pending_impl_case_classifies_designed_not_built(self) -> None:
        # PENDING-IMPL marker wins regardless of status or governs.
        (self.root / "scripts" / "half-shipped.py").write_text("# half\n", encoding="utf-8")
        path = _write_design(
            self.root, "pending.md", status="launched", governs=["scripts/half-shipped.py"],
            body="Half the surface ships. `[PENDING-IMPL]` — the rest lands later.",
        )
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["classification"], "designed-not-built")
        self.assertIn("PENDING-IMPL", item["reason"])

    def test_multiple_pending_impl_markers_yield_one_item_each(self) -> None:
        path = _write_design(
            self.root, "many-pending.md", status="draft", governs=[],
            body="First gap `[PENDING-IMPL]`.\nSecond gap `[PENDING-IMPL]` too.",
        )
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(all(i["classification"] == "designed-not-built" for i in result["items"]))

    def test_status_launched_but_unbuilt_classifies_wiki_tracked(self) -> None:
        # status: launched, but the governed target does not exist on disk,
        # and there is no PENDING-IMPL marker — agentmDesigns#11's fix.
        path = _write_design(
            self.root, "launched-not-built.md", status="launched",
            governs=["scripts/never-shipped.py"],
            body="Wiki says launched; the code was never written.",
        )
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["classification"], "wiki-tracked")
        self.assertIn("status is wiki-tracked", item["reason"])

    def test_status_launched_never_treated_as_built_signal_alone(self) -> None:
        # No governs at all, status launched -> still not "built".
        path = _write_design(self.root, "no-governs.md", status="launched", governs=[], body="No targets declared.")
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertNotEqual(result["items"][0]["classification"], "built")

    def test_draft_status_with_no_target_and_no_marker_is_designed_not_built(self) -> None:
        path = _write_design(self.root, "draft.md", status="draft", governs=[], body="Just an idea so far.")
        result = dvb.classify_design(path, self.root, "testrepo")
        self.assertEqual(result["items"][0]["classification"], "designed-not-built")


class TestWalkDesignsSkipsNavPages(unittest.TestCase):
    def test_index_pages_without_kind_design_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            designs = root / "wiki" / "designs"
            designs.mkdir(parents=True)
            (designs / "Designs.md").write_text("<!-- mode: index -->\n# Designs\n", encoding="utf-8")
            _write_design(root, "real.md", status="launched", governs=[])
            results = dvb.walk_designs(root, "testrepo")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["design"], "testrepo/real")


class TestBuildRegistryAndCheckRecords(unittest.TestCase):
    def test_missing_crickets_root_warns_but_still_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_design(root, "a.md", status="launched", governs=[])
            registry = dvb.build_registry(root, crickets_root=root / "does-not-exist")
            self.assertTrue(registry["warnings"])
            self.assertEqual(registry["total_designs"], 1)

    def test_to_check_records_marks_built_live_and_others_dark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "x.py").write_text("x\n", encoding="utf-8")
            _write_design(root, "built.md", status="launched", governs=["scripts/x.py"])
            _write_design(root, "pending.md", status="draft", governs=[], body="`[PENDING-IMPL]` gap.")
            registry = dvb.build_registry(root, crickets_root=None)
            records = dvb.to_check_records(registry)
            self.assertEqual(len(records), 2)
            by_pass = {r["check"]: r for r in records}
            built_record = next(r for r in records if "built::" in r["check"])
            pending_record = next(r for r in records if "pending::" in r["check"])
            self.assertIs(built_record["pass"], True)
            self.assertNotIn("dark", built_record)
            self.assertIsNone(pending_record["pass"])
            self.assertTrue(pending_record["dark"])
            del by_pass  # keep flake8 quiet about the unused convenience var


class TestMainExitsCleanOnLiveRepo(unittest.TestCase):
    def test_main_runs_stdlib_only_against_live_corpus(self) -> None:
        rc = dvb.main(["--agentm-root", str(dvb.REPO), "--format", "json"])
        self.assertEqual(rc, 0)

    def test_main_exits_2_on_missing_wiki_designs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = dvb.main(["--agentm-root", tmp])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
