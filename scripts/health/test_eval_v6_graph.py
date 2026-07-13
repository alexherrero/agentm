#!/usr/bin/env python3
"""Tests for scripts/health/eval_v6_graph.py's fail-loud-on-missing-fixture behavior.

L7 (proving-ledger seed item 5): a fixture entry whose source_path is missing
from the vault used to be silently excluded from the labeled sample (PR #287
shrank the sample from 145 to 95 true edges with no visible signal). This
asserts run_eval() still reports files_missing, and that main() now emits an
explicit failed record — never a silent pass — when any fixture file is gone.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import eval_v6_graph  # noqa: E402


class TestFailLoudOnMissingFixture(unittest.TestCase):
    def test_run_eval_reports_files_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "present.md").write_text("[[target-note]]", encoding="utf-8")

            fixture = vault / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "source_path": "present.md",
                                "target": "target-note",
                                "is_edge": True,
                                "edge_type": "reference",
                            },
                            {
                                "source_path": "gone.md",  # drifted / archived away
                                "target": "target-note",
                                "is_edge": True,
                                "edge_type": "reference",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = eval_v6_graph.run_eval(vault, fixture)
            self.assertEqual(result["files_missing"], 1)
            self.assertEqual(result["files_read"], 1)

    def test_main_fails_loud_instead_of_silently_shrinking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "present.md").write_text("[[target-note]]", encoding="utf-8")

            fixture = vault / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "source_path": "present.md",
                                "target": "target-note",
                                "is_edge": True,
                                "edge_type": "reference",
                            },
                            {"source_path": "gone.md", "target": "x", "is_edge": True, "edge_type": "reference"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            jsonl_out = vault / "out.jsonl"
            eval_v6_graph.main(
                ["--vault-path", str(vault), "--fixture", str(fixture), "--jsonl-out", str(jsonl_out)]
            )

            records = [json.loads(line) for line in jsonl_out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]["pass"], "a missing fixture file must never pass silently")
            self.assertIn("missing", records[0]["check"])


if __name__ == "__main__":
    unittest.main()
