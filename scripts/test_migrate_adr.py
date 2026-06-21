#!/usr/bin/env python3
"""Unit tests for migrate-adr — the ADR fold executor (AG Phase 2).

Temp-dir wiki fixtures; stdlib-only. Tests the plan/apply logic directly (never
shells out to check-all). Auto-discovered by the check-all unit gate.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# migrate-adr.py is not a valid module name (hyphen) — load it by path. Register
# in sys.modules BEFORE exec so @dataclass field-resolution works on Python 3.9.
_SPEC = importlib.util.spec_from_file_location(
    "migrate_adr", Path(__file__).resolve().parent / "migrate-adr.py"
)
ma = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate_adr"] = ma
_SPEC.loader.exec_module(ma)


def _scaffold(root: Path) -> None:
    """A minimal wiki: 2 ADRs, an index, a sidebar, a target design, prose refs."""
    dec = root / "wiki" / "decisions"
    des = root / "wiki" / "designs"
    exp = root / "wiki" / "explanation"
    for d in (dec, des, exp):
        d.mkdir(parents=True, exist_ok=True)
    (dec / "0009-on-host-state-mode-config.md").write_text("# ADR 0009\nbody\n", encoding="utf-8")
    (dec / "0012-vault-write-protocol.md").write_text("# ADR 0012\nbody\n", encoding="utf-8")
    (des / "memory-storage-seam.md").write_text(
        "---\nstatus: launched\narea: memory\ngoverns:\n  - scripts/storage_seam.py\n---\n# Seam\n",
        encoding="utf-8",
    )
    (dec / "Decisions.md").write_text(
        "<!-- mode: index -->\n# Decisions\n\n## Records\n\n"
        "- [ADR 0009 — On-host state-mode](0009-on-host-state-mode-config)\n"
        "- [ADR 0012 — Vault write protocol](0012-vault-write-protocol)\n"
        "- [ADR 0099 — Keeper](0099-keeper)\n",
        encoding="utf-8",
    )
    (dec / "_Sidebar.md").write_text(
        "### Decisions\n"
        "- [0009 — state-mode](0009-on-host-state-mode-config)\n"
        "- [0012 — write protocol](0012-vault-write-protocol)\n",
        encoding="utf-8",
    )
    # an ordinary content page: inline link (rewrite) + path-form link (rewrite) + prose (report)
    (exp / "Notes.md").write_text(
        "See [the protocol](0012-vault-write-protocol) and "
        "[state mode](../decisions/0009-on-host-state-mode-config).\n"
        "Historically ADR 0012 set the floor.\n",
        encoding="utf-8",
    )


def _fold_map() -> dict:
    return {
        "decisions_dir": "wiki/decisions",
        "index_files": ["wiki/decisions/Decisions.md", "wiki/decisions/_Sidebar.md"],
        "folds": [{
            "into_stem": "memory-storage-seam",
            "into": "wiki/designs/memory-storage-seam.md",
            "adrs": ["0009-on-host-state-mode-config", "0012-vault-write-protocol"],
        }],
    }


class TestHrefBasename(unittest.TestCase):
    def test_bare(self):
        self.assertEqual(ma._href_basename("0018-foo"), "0018-foo")
    def test_path_form(self):
        self.assertEqual(ma._href_basename("../decisions/0018-foo"), "0018-foo")
    def test_md_and_anchor(self):
        self.assertEqual(ma._href_basename("decisions/0018-foo.md#x"), "0018-foo")


class TestPlan(unittest.TestCase):
    def test_plan_detects_rewrites_prunes_prose_deletions(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            plan = ma.plan_fold(_fold_map(), root)
            self.assertEqual(plan.errors, [])
            # inline + path-form links in Notes.md → 2 rewrites
            rw = [r for r in plan.link_rewrites if r[0].endswith("Notes.md")]
            self.assertEqual(len(rw), 2)
            self.assertTrue(all(r[3] == "memory-storage-seam" for r in rw))
            # both index files prune both folded ADRs (Decisions.md keeps 0099)
            self.assertEqual(len(plan.index_prunes), 4)
            # prose "ADR 0012" reported
            self.assertTrue(any("ADR 0012" in c for _, _, c in plan.prose_refs))
            # both ADR files slated for deletion
            self.assertEqual(len(plan.deletions), 2)

    def test_missing_target_is_error(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            (root / "wiki" / "designs" / "memory-storage-seam.md").unlink()
            plan = ma.plan_fold(_fold_map(), root)
            self.assertTrue(any("target design missing" in e for e in plan.errors))

    def test_missing_adr_is_error(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            (root / "wiki" / "decisions" / "0009-on-host-state-mode-config.md").unlink()
            plan = ma.plan_fold(_fold_map(), root)
            self.assertTrue(any("ADR to fold missing" in e for e in plan.errors))

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            before = (root / "wiki" / "explanation" / "Notes.md").read_text()
            ma.plan_fold(_fold_map(), root)
            self.assertTrue((root / "wiki" / "decisions" / "0009-on-host-state-mode-config.md").is_file())
            self.assertEqual((root / "wiki" / "explanation" / "Notes.md").read_text(), before)


class TestApply(unittest.TestCase):
    def test_apply_rewrites_prunes_deletes_keeps_prose(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            ma.apply_fold(_fold_map(), root, ma.Plan())

            notes = (root / "wiki" / "explanation" / "Notes.md").read_text()
            # both links repointed to the bare target stem
            self.assertIn("[the protocol](memory-storage-seam)", notes)
            self.assertIn("[state mode](memory-storage-seam)", notes)
            self.assertNotIn("0012-vault-write-protocol", notes)
            self.assertNotIn("0009-on-host-state-mode-config", notes)
            # prose mention left intact (manual reconcile, not auto-rewritten)
            self.assertIn("ADR 0012 set the floor", notes)

            # index pruned of folded ADRs, keeper retained
            decisions = (root / "wiki" / "decisions" / "Decisions.md").read_text()
            self.assertNotIn("0009-on-host-state-mode-config", decisions)
            self.assertNotIn("0012-vault-write-protocol", decisions)
            self.assertIn("0099-keeper", decisions)

            # records retired
            self.assertFalse((root / "wiki" / "decisions" / "0009-on-host-state-mode-config.md").is_file())
            self.assertFalse((root / "wiki" / "decisions" / "0012-vault-write-protocol.md").is_file())

    def test_root_readme_swept_to_target_path(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            (root / "README.md").write_text(
                "See [protocol](wiki/decisions/0012-vault-write-protocol.md) "
                "and [state](wiki/decisions/0009-on-host-state-mode-config.md) "
                "and [other](wiki/decisions/0099-keeper.md).\n",
                encoding="utf-8",
            )
            ma.apply_fold(_fold_map(), root, ma.Plan())
            readme = (root / "README.md").read_text()
            # folded ADRs' relative links rewritten to the target's full repo-relative PATH
            self.assertIn("[protocol](wiki/designs/memory-storage-seam.md)", readme)
            self.assertIn("[state](wiki/designs/memory-storage-seam.md)", readme)
            self.assertNotIn("0012-vault-write-protocol", readme)
            self.assertNotIn("0009-on-host-state-mode-config", readme)
            # a non-folded ADR link is left untouched
            self.assertIn("(wiki/decisions/0099-keeper.md)", readme)

    def test_no_dangling_gated_links_after_apply(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _scaffold(root)
            ma.apply_fold(_fold_map(), root, ma.Plan())
            # no surviving bare-basename link to a folded ADR anywhere
            leftovers = []
            for md in (root / "wiki").rglob("*.md"):
                txt = md.read_text(encoding="utf-8")
                for m in ma.LINK_RE.finditer(txt):
                    if ma._href_basename(m.group(2)) in (
                        "0009-on-host-state-mode-config", "0012-vault-write-protocol"):
                        leftovers.append((md.name, m.group(0)))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
