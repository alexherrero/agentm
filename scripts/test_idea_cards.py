#!/usr/bin/env python3
"""test_idea_cards.py — an idea card in `personal/ideas/`, and the inbox's path
there (agentm-vault part 13).

What "filed as an idea" means, on the operator's rulings of 2026-09-20, and the
tempting violation beside each:

  - the card is `type: idea`, at the operator's group, `active` — not whatever
    the filing contract would route an idea to (`memory/semantic`, `unfiled`);
  - it loses `lifecycle` and `lifecycle_since` and keeps everything else it
    carried, its body byte for byte and its untrusted transport included;
  - an idea filed without a group is refused, not parked;
  - nothing already in the folder is overwritten, and nothing else under
    `personal/` is touched.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape  # noqa: E402
import idea_cards  # noqa: E402
import inbox_review  # noqa: E402

_SEMANTIC_IDEA = """---
title: Doom NPCs with a local Gemma brain
type: idea
summary: NPCs that act real on 8 GB local.
importance: 6
status: unfiled
lifecycle: dormant
lifecycle_since: 2026-09-11
filing_confidence: low
source: conversation
trust: trusted
created: 2026-06-07
updated: "2026-09-11"
tags: [games, local-llm]
slug: doom-llm-npcs
enriched_by: enrich/1+prompt/5d3a4cca1b02
enriched_at: "2026-09-11T23:55:49Z"
---

Take Doom and give its monsters a brain.

## Added by dreaming (2026-09-11)

The night's own section stays where it was.
"""

_INBOX_CARD = """---
title: Automate the PlayOn recordings
type: reference
summary: Recording by hand is tedious; the library could do it.
why: said while planning the movies library
importance: 5
status: unfiled
trust: untrusted
source: inbox
created: 2026-09-20
updated: 2026-09-20
tags: [movies]
---

We record streaming titles first to get them into the library early.
Look into automating PlayOn so it stops being a chore.
"""


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[1]


def _fields(text: str) -> dict:
    entries, _ = card_shape.split_note(text)
    return {k: card_shape.scalar(card_shape.raw_value(entries, k)) for k, _g in entries if k}


class AsIdeaCardTests(unittest.TestCase):
    def test_a_semantic_card_becomes_an_active_idea_at_its_group(self) -> None:
        out = idea_cards.as_idea_card(_SEMANTIC_IDEA, area="coding", today="2026-09-21")
        f = _fields(out)
        self.assertEqual(f["type"], "idea")
        self.assertEqual(f["area"], "coding")
        self.assertEqual(f["status"], "active")
        self.assertEqual(f["filing_confidence"], "high")
        self.assertEqual(f["updated"], "2026-09-21")
        self.assertNotIn("lifecycle", f)
        self.assertNotIn("lifecycle_since", f)
        self.assertNotIn("dismissed", f)
        # Everything else travels: the title, the provenance, the night's stamps.
        for key in ("title", "summary", "importance", "source", "trust", "created",
                    "tags", "slug", "enriched_by", "enriched_at"):
            self.assertIn(key, f, key)
        self.assertEqual(f["trust"], "trusted")
        # The body, byte for byte — the night's section included.
        self.assertEqual(_body(out), _body(_SEMANTIC_IDEA))
        # And in the card's order, so the gate reading the order is satisfied.
        self.assertEqual(card_shape.order_findings(card_shape.keys(out)), [])

    def test_a_struck_entry_carries_its_dismissal(self) -> None:
        out = idea_cards.as_idea_card(_SEMANTIC_IDEA, area="home-tech", dismissed="2026-05-24")
        self.assertEqual(_fields(out)["dismissed"], "2026-05-24")
        self.assertEqual(_fields(out)["status"], "active")

    def test_why_and_project_fill_only_an_empty_field(self) -> None:
        out = idea_cards.as_idea_card(_INBOX_CARD, area="home-tech",
                                      why="the operator's reason", project="movies-tv-games")
        f = _fields(out)
        self.assertEqual(f["why"], "said while planning the movies library",
                         "the room's why was replaced by the review's")
        self.assertEqual(f["project"], "movies-tv-games")
        bare = idea_cards.as_idea_card("---\ntitle: t\n---\n\nbody\n", area="coding", why="kept because")
        self.assertEqual(_fields(bare)["why"], "kept because")

    def test_a_group_is_one_lower_case_word(self) -> None:
        for bad in ("", "Home Tech", "home tech", "-home", "home/tech", "HOME"):
            with self.assertRaises(ValueError, msg=bad):
                idea_cards.as_idea_card(_INBOX_CARD, area=bad)
        for good in ("home-tech", "coding", "agentm", "blog", "other", "work2"):
            self.assertEqual(_fields(idea_cards.as_idea_card(_INBOX_CARD, area=good))["area"], good)

    def test_a_dismissal_is_a_day(self) -> None:
        with self.assertRaises(ValueError):
            idea_cards.as_idea_card(_INBOX_CARD, area="coding", dismissed="last spring")

    def test_a_card_with_no_block_gets_one_and_keeps_its_text(self) -> None:
        out = idea_cards.as_idea_card("A thought pasted in by hand.\n", area="other")
        self.assertEqual(_fields(out)["title"], "A thought pasted in by hand.")
        self.assertTrue(out.endswith("\n\nA thought pasted in by hand.\n"), out)


class FileAnIdeaFromTheInboxTests(unittest.TestCase):
    """The live layout: an Obsidian vault with the memory root at `agent/`."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.vault = Path(self._td.name)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "agent"
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(parents=True)
        (self.root / "memory" / "semantic").mkdir(parents=True)
        self.recipe = self.vault / "personal" / "Home" / "stew.md"
        self.recipe.parent.mkdir(parents=True)
        self.recipe.write_text("---\ntitle: Stew\n---\n\nBrown the meat.\n", encoding="utf-8")
        self.recipe_hash = hashlib.sha256(self.recipe.read_bytes()).hexdigest()

    def _drop(self, name: str, text: str) -> Path:
        p = self.inbox / name
        p.write_text(text, encoding="utf-8")
        return p

    def _nothing_else_moved(self) -> None:
        self.assertEqual(hashlib.sha256(self.recipe.read_bytes()).hexdigest(), self.recipe_hash,
                         "a note under personal/Home was written")
        self.assertEqual(list((self.root / "memory" / "semantic").iterdir()), [],
                         "the idea went where the contract routes an idea")

    def test_an_idea_is_filed_into_personal_ideas_under_its_group(self) -> None:
        self._drop("playon.md", _INBOX_CARD)
        out = inbox_review.file_one(self.root, "playon.md", type_hint="idea", area="home-tech")
        self.assertTrue(out["filed"], out["reason"])
        landed = self.vault / "personal" / "ideas" / "playon.md"
        self.assertEqual(Path(out["path"]), landed)
        text = landed.read_text(encoding="utf-8")
        f = _fields(text)
        self.assertEqual((f["type"], f["area"], f["status"], f["filing_confidence"]),
                         ("idea", "home-tech", "active", "high"))
        # The transport's stamps are the card's, and they travel.
        self.assertEqual((f["trust"], f["source"]), ("untrusted", "inbox"))
        self.assertNotIn("lifecycle", f)
        self.assertEqual(_body(text), _body(_INBOX_CARD))
        self.assertFalse((self.inbox / "playon.md").exists(), "the card stayed in the inbox")
        self._nothing_else_moved()

    def test_a_card_that_calls_itself_an_idea_goes_to_personal_ideas_too(self) -> None:
        self._drop("self-typed.md", _INBOX_CARD.replace("type: reference", "type: idea"))
        out = inbox_review.file_one(self.root, "self-typed.md", area="coding")
        self.assertTrue(out["filed"], out["reason"])
        self.assertTrue((self.vault / "personal" / "ideas" / "self-typed.md").is_file())
        self._nothing_else_moved()

    def test_an_idea_without_a_group_is_refused_and_stays_where_it_was(self) -> None:
        card = self._drop("playon.md", _INBOX_CARD)
        before = card.read_bytes()
        out = inbox_review.file_one(self.root, "playon.md", type_hint="idea")
        self.assertFalse(out["filed"])
        self.assertIn("--area", out["reason"])
        self.assertEqual(card.read_bytes(), before)
        self.assertFalse((self.vault / "personal" / "ideas").exists())
        self._nothing_else_moved()

    def test_a_group_on_a_card_that_is_not_an_idea_is_refused(self) -> None:
        card = self._drop("ref.md", _INBOX_CARD)
        out = inbox_review.file_one(self.root, "ref.md", type_hint="reference", area="coding")
        self.assertFalse(out["filed"])
        self.assertTrue(card.exists())

    def test_an_idea_already_in_the_folder_is_never_overwritten(self) -> None:
        existing = self.vault / "personal" / "ideas" / "playon.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("---\ntitle: mine\ntype: idea\narea: blog\n---\n\nMine.\n", encoding="utf-8")
        mine = existing.read_bytes()
        card = self._drop("playon.md", _INBOX_CARD)
        out = inbox_review.file_one(self.root, "playon.md", type_hint="idea", area="home-tech")
        self.assertFalse(out["filed"])
        self.assertIn("never overwritten", out["reason"])
        self.assertEqual(existing.read_bytes(), mine)
        self.assertTrue(card.exists(), "the inbox card was removed without landing")

    def test_the_cli_files_an_idea_with_its_group(self) -> None:
        self._drop("playon.md", _INBOX_CARD)
        rc = inbox_review.main(["--memory-root", str(self.root), "--file", "playon.md",
                                "--type", "idea", "--area", "home-tech"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.vault / "personal" / "ideas" / "playon.md").is_file())


if __name__ == "__main__":
    unittest.main()
