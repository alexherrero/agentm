#!/usr/bin/env python3
"""The residue line on the corpus scorecard (agentm-vault, landing group 02).

627 notes were purged as the six ruled manifests. 292 of them had survived an
earlier purge by being enriched into confident prose, which is why the count is
a standing line on the scorecard rather than a one-off migration report: a
number that climbs off zero names a writer that started producing residue
again.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import corpus_scorecard as cs  # noqa: E402
import purge  # noqa: E402


class TheResidueLine(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="residue-line-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / "memory" / "semantic").mkdir(parents=True)

    def _note(self, cls, name, body, **fm):
        d = self.vault / "memory" / cls
        d.mkdir(parents=True, exist_ok=True)
        head = "".join(f"{k}: {v}\n" for k, v in fm.items())
        (d / name).write_text(f"---\n{head}---\n\n{body}\n", encoding="utf-8")

    def test_a_clean_corpus_reads_zero(self):
        self._note("semantic", "keeper.md", "A real convention with a procedure.",
                   title="keeper", status="active")
        counts = cs.residue_counts(self.vault)
        self.assertEqual(sum(counts.values()), 0)
        self.assertIn("no tally", cs._residue_reading(self.vault).note)

    def test_every_residue_shape_is_counted(self):
        self._note("procedural", "tally.md", "The `Bash` tool was invoked 92 times during this session.",
                   title="t", status="active")
        self._note("semantic", "blurb.md", "A distributed file system.", title="b",
                   status="active", tags="[skill-discovery]", rubric_score="2")
        self._note("procedural", "fix.md", "Fix observed: the cache was stale.", title="f", status="active")
        self._note("crystallized", "supp.md", "User stated: never freeform.",
                   kind="opinion-supplement", status="proposed")
        self._note("semantic", "frag.md", "User stated: always squash merges.", title="u", status="active")
        self._note("semantic", "twin~dup.md", "a copy", title="d", status="active")
        counts = cs.residue_counts(self.vault)
        self.assertEqual(counts["tool-tallies"], 1)
        self.assertEqual(counts["skill-discovery-blurbs"], 1)
        self.assertEqual(counts["fix-observed-fragments"], 1)
        self.assertEqual(counts["opinion-supplements"], 1)
        self.assertEqual(counts["user-stated-fragments"], 1)
        self.assertEqual(counts["dup-twins"], 1)
        self.assertEqual(counts["status: proposed"], 1)
        self.assertEqual(sum(counts.values()), 7)  # the supplement is also `proposed`

    def test_the_soft_deleted_status_is_counted(self):
        # `deleted` is not in the vocabulary; one note carried it and was purged
        # on its own manifest. The line is what would catch a second.
        self._note("semantic", "smoke.md", "a smoke test", title="s", status="deleted")
        self.assertEqual(cs.residue_counts(self.vault)["status: deleted"], 1)

    def test_the_line_and_the_purge_lane_share_their_shapes(self):
        # one definition, two consumers: the line cannot drift from the thing
        # it measures.
        counts = cs.residue_counts(self.vault)
        for _, (title, _, _, _) in purge.POPULATIONS.items():
            self.assertIn(title, counts)

    def test_no_memory_dir_is_unavailable_not_zero(self):
        empty = Path(tempfile.mkdtemp(prefix="residue-empty-"))
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertIsNone(cs.residue_counts(empty))
        reading = cs._residue_reading(empty)
        self.assertTrue(reading.missing, "an absent memory/ must read as unavailable, not as zero")
        self.assertIsNone(reading.value)
        self.assertIn("not measured", reading.render())

    def test_the_reading_names_what_is_present(self):
        self._note("semantic", "twin~dup.md", "a copy", title="d", status="active")
        note = cs._residue_reading(self.vault).note
        self.assertIn("dup-twins 1", note)
        self.assertNotIn("tool-tallies", note)  # only what is actually there


if __name__ == "__main__":
    unittest.main()
