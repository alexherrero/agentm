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


if __name__ == "__main__":
    unittest.main()
