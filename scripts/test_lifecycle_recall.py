#!/usr/bin/env python3
"""The lifecycle axis in recall (filing v2 part 6, task 1).

A dormant note ranks below its active twin and stays served; an archived note
leaves everyday recall and answers the explicit archive query; a superseded
note never competes with its successor; pinned and active are served
untouched. The daemon path carries the same switch to the daemon's own wall.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recall  # noqa: E402

BODY = "The release gate waits for the checks to finish before the tag is pushed.\n"
QUERY = "release gate checks tag"


def _note(vault: Path, rel: str, lifecycle: str | None = None, *, status: str = "active",
          body: str = BODY) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntitle: Gate\nkind: reference\nstatus: {status}\n"
    if lifecycle:
        fm += f"lifecycle: {lifecycle}\n"
    p.write_text(fm + "---\n\n" + body, encoding="utf-8")
    return p


class _Vault(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="lifecycle-recall-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / "memory" / "semantic").mkdir(parents=True)

    def _rows(self, **kw):
        return recall.query(vault=self.vault, query_text=QUERY, k=5, **kw)

    def _paths(self, **kw):
        return [r["path"] for r in self._rows(**kw)]


class TheReading(unittest.TestCase):
    def test_one_reading_for_status_and_lifecycle(self):
        served = [{}, {"status": "active"}, {"lifecycle": "active"}, {"lifecycle": "dormant"},
                  {"lifecycle": "pinned"}, {"status": "unfiled"}]
        for fm in served:
            self.assertFalse(recall._unserved(fm), fm)
        self.assertTrue(recall._unserved({"lifecycle": "archived"}))
        self.assertFalse(recall._unserved({"lifecycle": "archived"}, include_archive=True))
        self.assertTrue(recall._unserved({"lifecycle": '"Archived"'}), "quoted, cased value still reads")
        self.assertTrue(recall._unserved({"lifecycle": "superseded"}))
        self.assertTrue(recall._unserved({"lifecycle": "superseded"}, include_archive=True),
                        "the explicit archive query lifts the archive wall, never the successor's")
        for st in recall._UNSERVED_STATUSES:
            self.assertTrue(recall._unserved({"status": st, "lifecycle": "active"}), st)


class TheInProcessArm(_Vault):
    def test_a_dormant_twin_ranks_below_its_active_twin_and_stays_served(self):
        # The dormant twin sorts first by path, so a tie would put it on top:
        # the ordering is the demotion, not the tiebreak.
        _note(self.vault, "memory/semantic/a-dormant.md", "dormant")
        _note(self.vault, "memory/semantic/b-active.md", "active")
        self.assertEqual(self._paths(), ["memory/semantic/b-active.md", "memory/semantic/a-dormant.md"])

    def test_the_demotion_is_visible_on_the_row(self):
        _note(self.vault, "memory/semantic/a-dormant.md", "dormant")
        _note(self.vault, "memory/semantic/b-active.md", "active")
        rows = self._rows()
        dormant = next(r for r in rows if r["path"].endswith("a-dormant.md"))
        active = next(r for r in rows if r["path"].endswith("b-active.md"))
        self.assertEqual(dormant.get("lifecycle"), "dormant")
        self.assertAlmostEqual(dormant["decay_score"], recall._LIFECYCLE_DEMOTION, places=6)
        self.assertNotIn("lifecycle", active)
        # The fused base differs by rank (RRF), so the twins never share one;
        # what the demotion promises is the order, and that it is a multiplier
        # on the dormant row alone.
        self.assertLess(dormant["combined"], active["combined"])

    def test_an_archived_note_leaves_everyday_recall_and_answers_the_explicit_query(self):
        _note(self.vault, "memory/semantic/a-archived.md", "archived")
        _note(self.vault, "memory/semantic/b-active.md", "active")
        self.assertEqual(self._paths(), ["memory/semantic/b-active.md"])
        # Brought back, the archived twin is present and demoted — never
        # restored to parity with the active one (the daemon does the same).
        self.assertEqual(self._paths(include_archive=True),
                         ["memory/semantic/b-active.md", "memory/semantic/a-archived.md"])
        rows = self._rows(include_archive=True)
        self.assertEqual(rows[1].get("lifecycle"), "archived")
        self.assertTrue((self.vault / "memory/semantic/a-archived.md").exists(), "on disk, in place")

    def test_an_archived_note_that_is_the_only_answer_is_still_absent(self):
        _note(self.vault, "memory/semantic/only.md", "archived")
        self.assertEqual(self._paths(), [])
        self.assertEqual(self._paths(include_archive=True), ["memory/semantic/only.md"])

    def test_superseded_never_competes_even_on_the_explicit_query(self):
        _note(self.vault, "memory/semantic/a-old.md", "superseded")
        _note(self.vault, "memory/semantic/b-new.md", "active")
        self.assertEqual(self._paths(include_archive=True), ["memory/semantic/b-new.md"])

    def test_pinned_and_active_twins_tie(self):
        _note(self.vault, "memory/semantic/a-pinned.md", "pinned")
        _note(self.vault, "memory/semantic/b-active.md", "active")
        rows = self._rows()
        # Path order, which is what equal treatment looks like here: a demoted
        # pinned twin would sort below, and a lifted one would carry a key it
        # must not have. Pinned is the absence of a demotion, never a lift.
        self.assertEqual([r["path"] for r in rows],
                         ["memory/semantic/a-pinned.md", "memory/semantic/b-active.md"])
        for r in rows:
            self.assertNotIn("lifecycle", r)


class TheDaemonPath(_Vault):
    def _argv(self, **kw) -> list:
        seen: dict = {}

        def fake_run(argv, *a, **k):
            seen["argv"] = list(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"results": []}), stderr="")

        with mock.patch.object(recall.subprocess, "run", side_effect=fake_run):
            recall._daemon_search(vault=self.vault, query_text="release gate checks", k=5, drops={}, **kw)
        self.assertIn("search", seen.get("argv", []), "the daemon was not asked")
        return seen["argv"]

    def test_the_explicit_archive_query_reaches_the_daemons_own_wall(self):
        self.assertIn("-include-archived", self._argv(include_archive=True))
        self.assertNotIn("-include-archived", self._argv())


if __name__ == "__main__":
    unittest.main()
