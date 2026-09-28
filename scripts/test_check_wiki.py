#!/usr/bin/env python3
"""Tests for check-wiki.py's --jsonl-out flag (AA5 C7 — docs+voice health axis)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent


def _load():
    spec = importlib.util.spec_from_file_location("check_wiki_jsonl", _SCRIPTS / "check-wiki.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_wiki_jsonl"] = mod
    spec.loader.exec_module(mod)
    return mod


cw = _load()


class TestJsonlOut(unittest.TestCase):
    def test_clean_wiki_emits_live_pass_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = Path(tmp) / "wiki"
            wiki_root.mkdir()
            (wiki_root / "Home.md").write_text("# Home\n\n[[Home]]\n")
            (wiki_root / "_Sidebar.md").write_text("[[Home]]\n")
            out = Path(tmp) / "out.jsonl"
            argv = ["check-wiki.py", "--strict", "--no-readme",
                    "--root", str(wiki_root), "--jsonl-out", str(out)]
            with patch.object(sys, "argv", argv):
                rc = cw.main()
            self.assertEqual(rc, 0)
            lines = out.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["suite"], "check-wiki")
            self.assertEqual(record["axis"], "docs+voice health")
            self.assertEqual(record["check"], "structural-cleanliness")
            self.assertIs(record["pass"], True)
            self.assertEqual(record["weight"], 5)

    def test_hard_issue_under_strict_emits_failing_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = Path(tmp) / "wiki"
            wiki_root.mkdir()
            # Two files with the same stem in different dirs trips rule g
            # (unique basenames) -- a hard issue.
            (wiki_root / "how-to").mkdir()
            (wiki_root / "reference").mkdir()
            (wiki_root / "how-to" / "Dup.md").write_text("# Dup\n")
            (wiki_root / "reference" / "Dup.md").write_text("# Dup\n")
            out = Path(tmp) / "out.jsonl"
            argv = ["check-wiki.py", "--strict", "--no-readme",
                    "--root", str(wiki_root), "--jsonl-out", str(out)]
            with patch.object(sys, "argv", argv):
                rc = cw.main()
            self.assertEqual(rc, 1)
            record = json.loads(out.read_text().splitlines()[0])
            self.assertIs(record["pass"], False)

    def test_no_jsonl_out_is_a_silent_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = Path(tmp) / "wiki"
            wiki_root.mkdir()
            (wiki_root / "Home.md").write_text("# Home\n\n[[Home]]\n")
            (wiki_root / "_Sidebar.md").write_text("[[Home]]\n")
            argv = ["check-wiki.py", "--strict", "--no-readme", "--root", str(wiki_root)]
            with patch.object(sys, "argv", argv):
                rc = cw.main()
            self.assertEqual(rc, 0)  # no --jsonl-out, no file, no crash


class TestRuleRImagesResolve(unittest.TestCase):
    """An image path is resolved relative to the page, the way the publish
    step resolves it. crickets shipped seventeen broken diagrams for months
    after ten pages moved sections and their images stayed behind; the rule
    is the same in both repos."""

    def _issues(self, page_rel: str, text: str, files: tuple = ()):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in files:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_text("<svg/>", encoding="utf-8")
            page = root / page_rel
            page.parent.mkdir(parents=True, exist_ok=True)
            issues = []
            cw.rule_r_images_resolve(page, text, issues)
            return issues

    def test_an_image_the_page_moved_away_from_fires(self):
        issues = self._issues("explanation/Page.md", "![flow](diagrams/flow.svg)\n",
                              files=("reference/diagrams/flow.svg",))
        self.assertEqual([i.rule for i in issues], ["r"])

    def test_a_path_that_resolves_from_the_page_passes(self):
        issues = self._issues("explanation/Page.md", "![flow](../reference/diagrams/flow.svg)\n",
                              files=("reference/diagrams/flow.svg",))
        self.assertEqual(issues, [])

    def test_external_images_code_spans_and_fences_are_exempt(self):
        text = ("![badge](https://img.shields.io/x.svg)\n"
                "`![not an image](missing.svg)`\n"
                "```\n![example](also-missing.svg)\n```\n")
        self.assertEqual(self._issues("explanation/Page.md", text), [])


if __name__ == "__main__":
    unittest.main()
