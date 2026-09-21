#!/usr/bin/env python3
"""test_idea_cards_migration.py — the one-time move of the ideas (agentm-vault
plan 13), driven over a scratch vault in the live layout.

The operator's rulings of 2026-09-20, each against the write that would break
it: an idea moves to `personal/ideas/` active at its group with its words byte
for byte; a duplicate stays where it is, superseded, naming its survivor; a
relabelled note changes its type and nothing else; an entry becomes a card with
its date, its dismissal and its links re-pointed; the deletion list is written
out and nothing is deleted; and the whole run is refused while a writer is live,
on a wrong count, or when a note moved since the plan — and undone by its
journal.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE / "migrate", _HERE.parent / "harness" / "skills" / "memory" / "scripts", _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import card_shape  # noqa: E402
import ideas_migration as mig  # noqa: E402  (scripts/migrate/ideas_migration.py)

TODAY = "2026-09-21"

GRADUATE = """---
title: Doom NPCs with a local Gemma brain
type: idea
status: active
lifecycle: active
filing_confidence: low
source: conversation
trust: trusted
created: 2026-06-07
updated: 2026-09-11
tags: [games]
slug: graduate
---

Give Doom's monsters a local model for a brain.
"""

TWIN = """---
title: doom npcs again
type: idea
status: unfiled
lifecycle: active
filing_confidence: low
source: conversation
trust: trusted
created: 2026-06-08
updated: 2026-06-08
---

...give the monsters a brain...
"""

INSIGHT = """---
title: Read-all versus search is a corpus-size decision
type: idea
status: active
lifecycle: active
filing_confidence: high
source: external-fetch
trust: untrusted
created: 2026-08-11
updated: 2026-08-11
---

Handing a model every memory beats retrieval while the corpus fits.
"""

SCRAP = """---
type: idea
status: unfiled
lifecycle: active
filing_confidence: low
source: conversation
trust: trusted
created: 2026-07-11
updated: 2026-07-11
---

...let's do follow-up c...
"""

NOT_AN_IDEA = """---
title: A reference that is not an idea
type: reference
status: active
created: 2026-01-01
updated: 2026-01-01
---

Left alone.
"""

IDEAS_MD = """# Ideas

Hand-kept preamble.

---

## 2026-05-24: Migrate the family mail to one account

Move every address onto one mailbox before anything else touches identity. See [[_idea-incubator/graduate/_summary]] and the [[personal-projects/home-tech-next/_index|Home Tech Next]] plan.

---

## 2026-06-07: Doom NPCs powered by a local AI model

Take Doom and give its monsters a local model.

**Feasibility (2026-06-07):** a two-tier split keeps the frame rate — the planner runs between frames and the shooter never waits.

---

## ~~2026-05-20: Unraid GitHub Actions runner~~ — Dismissed 2026-05-24

~~Run CI on the home server.~~

---
"""

MAPPING = """# mapping

| # | verdict | group | slug or card | entry | note |
|---|---|---|---|---|---|
| E01 | new | home-tech | `migrate-family-mail` | 2026-05-24 · Migrate | Home. |
| E02 | absorbed:graduate | coding | `graduate` | 2026-06-07 · Doom | |
| E03 | new, dismissed 2026-05-24 | home-tech | `unraid-github-actions-runner` | 2026-05-20 · Runner | |

| id | verdict | group | title | why |
|---|---|---|---|---|
| `graduate` | idea | coding | Doom | |
| `twin` | dup:graduate | | twin | |
| `insight` | relabel:reference | | insight | |

| id | verdict | title | why | links in |
|---|---|---|---|---|
| `scrap` | delete | (untitled) | a fragment | 0 |
"""


def _fields(text: str) -> dict:
    entries, _ = card_shape.split_note(text)
    return {k: card_shape.scalar(card_shape.raw_value(entries, k)) for k, _g in entries if k}


class _Vault(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.vault = Path(self._td.name)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "agent"
        sem = self.root / "memory" / "semantic"
        sem.mkdir(parents=True)
        for name, text in (("graduate", GRADUATE), ("twin", TWIN), ("insight", INSIGHT),
                           ("scrap", SCRAP), ("a-reference", NOT_AN_IDEA)):
            (sem / f"{name}.md").write_text(text, encoding="utf-8")
        (self.vault / "Ideas.md").write_text(IDEAS_MD, encoding="utf-8")
        (self.vault / "projects" / "home-tech-next").mkdir(parents=True)
        (self.vault / "projects" / "home-tech-next" / "charter.md").write_text("# charter\n", encoding="utf-8")
        self.recipe = self.vault / "personal" / "Home" / "stew.md"
        self.recipe.parent.mkdir(parents=True)
        self.recipe.write_text("Brown the meat.\n", encoding="utf-8")

    def _plan(self, mapping: str = MAPPING) -> dict:
        return mig.build_plan(self.vault, self.root, mapping, today=TODAY)

    def _read(self, rel: str) -> str:
        return (self.vault / rel).read_text(encoding="utf-8")

    def _snapshot(self) -> dict:
        return {p.relative_to(self.vault).as_posix(): p.read_bytes()
                for p in sorted(self.vault.rglob("*")) if p.is_file() and ".obsidian" not in p.parts}


class ThePlan(_Vault):
    def test_every_verdict_becomes_its_write_and_the_scraps_a_list(self) -> None:
        plan = self._plan()
        kinds = sorted((o["op"], o["to"]) for o in plan["ops"])
        self.assertEqual(kinds, [
            ("create", "personal/ideas/migrate-family-mail.md"),
            ("create", "personal/ideas/unraid-github-actions-runner.md"),
            ("edit", "agent/memory/semantic/insight.md"),
            ("edit", "agent/memory/semantic/twin.md"),
            ("move", "personal/ideas/graduate.md"),
        ])
        self.assertEqual(plan["deletions"], ["memory/semantic/scrap.md"])
        self.assertEqual(plan["count"], 5)
        # The dry run wrote nothing.
        self.assertFalse((self.vault / "personal" / "ideas").exists())
        self.assertTrue((self.root / "memory" / "semantic" / "scrap.md").exists())

    def test_a_mapping_that_misses_a_card_or_an_entry_is_refused(self) -> None:
        missing = MAPPING.replace("| `insight` | relabel:reference | | insight | |\n", "")
        with self.assertRaises(mig.Refused) as cm:
            self._plan(missing)
        self.assertIn("insight", str(cm.exception))
        no_entry = MAPPING.replace("| E03 | new, dismissed 2026-05-24 | home-tech | `unraid-github-actions-runner` | 2026-05-20 · Runner | |\n", "")
        with self.assertRaises(mig.Refused) as cm:
            self._plan(no_entry)
        self.assertIn("E03", str(cm.exception))

    def test_a_bad_group_verdict_or_survivor_is_refused(self) -> None:
        for bad, word in ((MAPPING.replace("| idea | coding |", "| idea | Home Tech |"), "group"),
                          (MAPPING.replace("dup:graduate", "dup:nobody"), "survivor"),
                          (MAPPING.replace("relabel:reference", "relabel:essay"), "essay"),
                          (MAPPING.replace("| delete |", "| maybe |"), "maybe")):
            with self.subTest(word=word), self.assertRaises(mig.Refused) as cm:
                self._plan(bad)
            self.assertIn(word, str(cm.exception))

    def test_an_idea_card_already_in_the_folder_is_never_overwritten(self) -> None:
        (self.vault / "personal" / "ideas").mkdir(parents=True)
        (self.vault / "personal" / "ideas" / "graduate.md").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(mig.Refused) as cm:
            self._plan()
        self.assertIn("never overwritten", str(cm.exception))


class TheApply(_Vault):
    def test_the_move_lands_every_ruling(self) -> None:
        plan = self._plan()
        journal = self.vault.parent / f"{self.vault.name}-journal.jsonl"
        self.addCleanup(lambda: journal.unlink(missing_ok=True))
        self.assertEqual(mig.apply(plan, 5, journal, writers=lambda: []), 5)

        moved = self._read("personal/ideas/graduate.md")
        f = _fields(moved)
        self.assertEqual((f["type"], f["area"], f["status"], f["filing_confidence"]),
                         ("idea", "coding", "active", "high"))
        self.assertNotIn("lifecycle", f)
        self.assertFalse((self.root / "memory" / "semantic" / "graduate.md").exists())
        # Its words byte for byte, then what the entry held that it did not.
        self.assertIn("\nGive Doom's monsters a local model for a brain.\n", moved)
        self.assertIn("## From Ideas.md (2026-06-07)", moved)
        self.assertIn("two-tier split keeps the frame rate", moved)
        self.assertEqual(f["summary"], "Take Doom and give its monsters a local model.")

        twin = _fields(self._read("agent/memory/semantic/twin.md"))
        self.assertEqual((twin["lifecycle"], twin["superseded_by"], twin["lifecycle_since"]),
                         ("superseded", "personal/ideas/graduate.md", TODAY))
        self.assertEqual(twin["type"], "idea", "a duplicate is superseded, not relabelled")

        insight = self._read("agent/memory/semantic/insight.md")
        self.assertEqual(_fields(insight)["type"], "reference")
        self.assertEqual(insight.replace("type: reference", "type: idea"), INSIGHT,
                         "a relabel changed more than the type")

        mail = self._read("personal/ideas/migrate-family-mail.md")
        mf = _fields(mail)
        self.assertEqual((mf["title"], mf["created"], mf["area"], mf["status"], mf["source"]),
                         ("Migrate the family mail to one account", "2026-05-24", "home-tech", "active",
                          "operator-direct"))
        self.assertIn("[[graduate]]", mail, "the incubator link was not re-pointed at the card")
        self.assertIn("[[projects/home-tech-next/charter|Home Tech Next]]", mail)
        self.assertIn("projects/home-tech-next/charter|Home Tech Next", mf["related"])

        runner = _fields(self._read("personal/ideas/unraid-github-actions-runner.md"))
        self.assertEqual(runner["dismissed"], "2026-05-24")

        # The scraps are a list, not a deletion; the rest is untouched.
        self.assertTrue((self.root / "memory" / "semantic" / "scrap.md").exists())
        self.assertEqual(self._read("agent/memory/semantic/a-reference.md"), NOT_AN_IDEA)
        self.assertEqual(self.recipe.read_text(encoding="utf-8"), "Brown the meat.\n")
        self.assertEqual(self._read("Ideas.md"), IDEAS_MD, "the migration wrote Ideas.md")

    def test_refused_while_a_writer_is_live_or_on_a_wrong_count(self) -> None:
        plan = self._plan()
        before = self._snapshot()
        journal = self.vault.parent / f"{self.vault.name}-j2.jsonl"
        self.addCleanup(lambda: journal.unlink(missing_ok=True))
        with self.assertRaises(mig.Refused) as cm:
            mig.apply(plan, 5, journal, writers=lambda: ["Obsidian"])
        self.assertIn("Obsidian", str(cm.exception))
        with self.assertRaises(mig.Refused):
            mig.apply(plan, 4, journal, writers=lambda: [])
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(journal.exists())

    def test_a_note_that_moved_since_the_plan_refuses_the_whole_run(self) -> None:
        plan = self._plan()
        (self.root / "memory" / "semantic" / "twin.md").write_text(TWIN + "\nan edit\n", encoding="utf-8")
        before = self._snapshot()
        journal = self.vault.parent / f"{self.vault.name}-j3.jsonl"
        self.addCleanup(lambda: journal.unlink(missing_ok=True))
        with self.assertRaises(mig.Refused) as cm:
            mig.apply(plan, 5, journal, writers=lambda: [])
        self.assertIn("twin", str(cm.exception))
        self.assertEqual(self._snapshot(), before)

    def test_the_journal_undoes_the_run_byte_for_byte(self) -> None:
        before = self._snapshot()
        plan = self._plan()
        journal = self.vault.parent / f"{self.vault.name}-j4.jsonl"
        self.addCleanup(lambda: journal.unlink(missing_ok=True))
        mig.apply(plan, 5, journal, writers=lambda: [])
        self.assertNotEqual(self._snapshot(), before)
        self.assertEqual(mig.revert(journal, self.vault), 5)
        after = self._snapshot()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
