#!/usr/bin/env python3
"""The vault-size line: the vault's notes by area, and how they moved.

Task 199. The morning note carries one line counting the vault's Markdown
notes in each area, with the change since yesterday, and a warning when
memory grows more than 15 a day over a week, the vault more than 60 net in a
day, or one area more than 100 in a day. The count reads the vault's git
history, so each test builds a scratch vault in the live layout
(`agent/memory/...` beside `projects/...`), commits notes at chosen times and
holds the line to them: the counts, the changes, each warning just over its
bar and quiet at it, a vault too young to compare, and the morning note's seam.

Run: python3 scripts/test_vault_size.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import morning_note  # noqa: E402
import vault_size as vs  # noqa: E402

NOW = datetime(2026, 11, 8, 6, 0).timestamp()
YESTERDAY = NOW - vs.DAY - 3600
LAST_WEEK = NOW - vs.WEEK * vs.DAY - 3600
TONIGHT = NOW - 3600


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vault-size-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        empty = self.tmp / "gitconfig"
        empty.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(key, None)
        self.vault = self.tmp / "vault"
        self.mem = self.vault / "agent"
        self.mem.mkdir(parents=True)
        self.git("init", "-q", str(self.vault), cwd=self.tmp)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self._n = 0

    def git(self, *args, cwd=None):
        env = dict(os.environ)
        env.update(getattr(self, "_dates", {}))
        subprocess.run(["git", *args], cwd=cwd or self.vault, env=env, check=True,
                       capture_output=True, text=True)

    def notes(self, folder: str, n: int, ext: str = ".md"):
        for _ in range(n):
            self._n += 1
            p = self.vault / folder / f"n{self._n}{ext}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# note\n", encoding="utf-8")

    def commit(self, at: float):
        stamp = f"@{int(at)} +0000"
        self._dates = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "vault: local edits")
        self._dates = {}

    def seed(self):
        """A week-old vault: a note in each area and two at the root."""
        for folder in ("agent/memory/semantic", "agent/diagnostics/morning", "projects/p",
                       "personal", "resources", "systems", "calendar/2026", "standards"):
            self.notes(folder, 1)
        self.notes("", 2)
        self.commit(LAST_WEEK)


class TheCounts(_Vault):
    def test_each_area_counts_its_notes_and_the_total_counts_every_note(self):
        self.seed()
        self.notes("agent/memory/procedural", 2)
        self.notes("agent/inbox", 1)          # in no area, still in the total
        self.notes("projects/p", 1, ext=".json")  # not a note
        self.commit(TONIGHT)
        m = vs.measure(self.mem, now=NOW)
        self.assertEqual(m["now"]["memory"], 3)
        self.assertEqual(m["now"]["projects"], 1)
        self.assertEqual(m["now"]["calendar"], 1)
        self.assertEqual(m["now"]["total"], 13)

    def test_the_line_shows_the_change_since_yesterday_and_only_where_there_was_one(self):
        self.seed()
        self.notes("projects/p", 3)
        self.commit(YESTERDAY)
        self.notes("agent/memory/semantic", 2)
        self.commit(TONIGHT)
        line = vs.describe(vs.measure(self.mem, now=NOW))
        self.assertTrue(line.startswith("15 notes (+2) — memory 3 (+2) · diagnostics 1 · projects 4 ·"), line)
        self.assertIn("memory +0.3 a day over 7 days", line)
        self.assertNotIn("⚠", line)

    def test_a_flat_vault_finds_memory_at_its_root(self):
        self.notes("memory/semantic", 2)
        self.notes("projects/p", 1)
        self.commit(TONIGHT)
        m = vs.measure(self.vault, now=NOW)
        self.assertEqual((m["now"]["memory"], m["now"]["projects"]), (2, 1))


class TheWarnings(_Vault):
    def test_the_vault_growing_over_60_in_a_day_is_a_warning_and_60_is_not(self):
        self.seed()
        self.commit(YESTERDAY)
        self.notes("projects/a", 30)
        self.notes("resources", 30)
        self.commit(TONIGHT)
        self.assertEqual(vs.measure(self.mem, now=NOW)["warnings"], [])
        self.notes("resources", 1)
        self.commit(TONIGHT + 60)
        self.assertEqual(vs.measure(self.mem, now=NOW)["warnings"],
                         ["the vault grew 61 notes in a day, over 60"])

    def test_one_area_growing_over_100_in_a_day_is_a_warning(self):
        self.seed()
        self.notes("projects/p", 200)
        self.commit(YESTERDAY)
        self.notes("projects/p", 101)
        self.notes("personal", 100)
        self.commit(TONIGHT)
        warnings = vs.measure(self.mem, now=NOW)["warnings"]
        self.assertIn("projects grew 101 in a day, over 100", warnings)
        self.assertFalse(any(w.startswith("personal") for w in warnings), warnings)

    def test_memory_over_15_a_day_across_the_week_is_a_warning(self):
        self.seed()
        self.notes("agent/memory/semantic", 105)   # exactly 15 a day
        self.commit(YESTERDAY)
        self.assertEqual(vs.measure(self.mem, now=NOW)["warnings"], [])
        self.notes("agent/memory/semantic", 1)
        self.commit(TONIGHT)
        line = vs.describe(vs.measure(self.mem, now=NOW))
        self.assertIn("— ⚠ memory grew 15.1 a day over 7 days, over 15", line)

    def test_shrinking_is_never_a_warning(self):
        self.seed()
        self.notes("agent/memory/semantic", 300)
        self.commit(YESTERDAY)
        shutil.rmtree(self.vault / "agent" / "memory" / "semantic")
        self.commit(TONIGHT)
        m = vs.measure(self.mem, now=NOW)
        self.assertEqual(m["warnings"], [])
        self.assertIn("memory 0 (-301)", vs.describe(m))


class TheAbsences(_Vault):
    def test_a_vault_younger_than_a_day_says_so_instead_of_a_change(self):
        self.notes("agent/memory/semantic", 1)
        self.commit(TONIGHT)
        m = vs.measure(self.mem, now=NOW)
        self.assertEqual((m["day"], m["week"], m["warnings"]), (None, None, []))
        self.assertIn("no commit a day old to compare against", vs.describe(m))

    def test_outside_git_it_is_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError, "not a git working tree"):
                vs.measure(Path(d), now=NOW)

    def test_a_vault_with_no_commit_is_not_measured(self):
        with self.assertRaisesRegex(RuntimeError, "no commit"):
            vs.measure(self.mem, now=NOW)


class TheMorningNote(_Vault):
    def test_the_corpus_section_carries_one_size_line(self):
        self.seed()
        self.commit(YESTERDAY)
        self.notes("calendar/2026", 1)
        self.commit(TONIGHT)
        night = morning_note.Night(now=NOW, start=NOW - 4 * 3600)
        night.reasons = {job: "not tonight" for job, _label in morning_note.NIGHT_JOBS}
        morning_note.read_size(night, self.mem)
        lines = morning_note.size_line(night)
        self.assertEqual(lines, [f"- **Vault size:** {vs.describe(night.size)}"])
        text, _head = morning_note.render(night, vault=self.mem)
        corpus = text.split("## The corpus", 1)[1]
        self.assertIn("- **Vault size:** 11 notes (+1) — memory 1 ·", corpus)

    def test_the_note_says_when_the_size_was_not_measured(self):
        night = morning_note.Night(now=NOW, start=NOW - 3600)
        with tempfile.TemporaryDirectory() as d:
            morning_note.read_size(night, Path(d))
        self.assertTrue(morning_note.size_line(night)[0].startswith("- Vault size not measured ("))


if __name__ == "__main__":
    unittest.main()
