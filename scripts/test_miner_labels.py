#!/usr/bin/env python3
"""The operator's labels on the write path's sample, as a regression fixture
(miner-provenance, task 5). Twenty decisions from session e805ab79 were
labeled on 2026-09-04; the four rulings that followed are pinned here against
the rows that produced them, so the miner cannot drift back:

- the eight tool-invocation stubs are never candidates again (ruling 2);
- the five "never …" rows came from a pasted handoff, replayed in
  test_reflect_handoff_marker.py (ruling 1);
- the three fix fragments mine to nothing (ruling 3, in
  test_reflect_fix_and_durability.py);
- the operator's own short lines still mine, and the one labeled
  should-not-file — an in-the-moment request — no longer files HIGH (ruling 4).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402

FIXTURE = _HERE / "health" / "fixtures" / "write-path-labels" / "e805ab79.json"


class TheLabeledRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.rows = cls.fx["rows"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="miner-labels-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _mine_user(self, text):
        p = self.tmp / "t.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n", encoding="utf-8")
        return reflect.mine_transcript(p)["memory_candidates"]

    def test_the_sheet_had_twenty_rows_and_every_row_is_labeled(self):
        self.assertEqual(len(self.rows), 20)
        self.assertTrue(all(r["label"] in self.fx["labels"] for r in self.rows))

    def test_the_stubs_are_gone_from_the_miner(self):
        stubs = [r for r in self.rows if r["category"] == "workflow"]
        self.assertEqual(len(stubs), 8)
        p = self.tmp / "t.jsonl"
        p.write_text("\n".join(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash"}]}}) for _ in range(2592)) + "\n", encoding="utf-8")
        out = reflect.mine_transcript(p)
        self.assertEqual([c for c in out["memory_candidates"] if c.category == "workflow"], [])
        self.assertEqual(out["tool_counts"], {"Bash": 2592})

    def test_the_operators_own_lines_still_mine_and_the_request_is_not_high(self):
        replayable = [r for r in self.rows if (r.get("source") or {}).get("text")]
        self.assertEqual(len(replayable), 4, [r["row"] for r in replayable])
        for r in replayable:
            cands = self._mine_user(r["source"]["text"])
            prefs = [c for c in cands if c.category == "preferences"]
            self.assertTrue(prefs, f"row {r['row']} no longer mines at all")
            if r["label"] == "should-not-file":
                self.assertTrue(all(c.confidence != "HIGH" for c in prefs),
                                f"row {r['row']} (should-not-file) still files HIGH: {[(c.title, c.confidence) for c in prefs]}")

    def test_the_pasted_rows_and_the_fragments_are_pinned_elsewhere(self):
        pasted = [r for r in self.rows if r["category"] == "preferences" and r["confidence"] == "LOW"]
        fixes = [r for r in self.rows if r["category"] == "fix"]
        self.assertEqual((len(pasted), len(fixes)), (5, 3))
        self.assertTrue(all(r["source"]["index"] == 6 for r in pasted))


if __name__ == "__main__":
    unittest.main()
