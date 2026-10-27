#!/usr/bin/env python3
"""The Python writers of the link-valued keys (task 193 step 7, LD-5).

`supersedes`, `superseded_by`, `related` and `derived_from` are written as
quoted wikilinks by vault path. For each writer this pins two things: the line
it writes parses with PyYAML to a string, or a flat list of strings, holding
`[[…]]` (the census's rule 4: what Obsidian draws as a link), and step 6's
parser reads it back as the note it meant.

Run directly:

    python3 scripts/test_link_writers.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import calendar_facets  # noqa: E402
import correction  # noqa: E402
import evolve  # noqa: E402
import filing_engine  # noqa: E402
import note_links  # noqa: E402
import save  # noqa: E402
import supersession_migrate  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), text
    end = text.index("\n---\n", 3)
    return yaml.safe_load(text[4:end]) or {}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class LinkWriters(unittest.TestCase):

    def setUp(self):
        self.obs = Path(tempfile.mkdtemp(prefix="link-writers-"))
        self.addCleanup(shutil.rmtree, self.obs, ignore_errors=True)
        (self.obs / ".obsidian").mkdir()
        self.root = self.obs / "agent"
        for rel in ("memory/semantic/old.md", "memory/semantic/new.md", "memory/semantic/a.md",
                    "memory/semantic/b.md", "memory/episodic/trace.md"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(f"---\ntitle: {Path(rel).stem}\ntype: fact\nstatus: active\n---\n\nBody.\n",
                                         encoding="utf-8")

    def assertLinks(self, text: str, key: str, want: list):
        """The key parses as a string or a flat list of strings holding `[[…]]`,
        and reads back as the memory-root paths `want`."""
        value = _frontmatter(text)[key]
        items = value if isinstance(value, list) else [value]
        for item in items:
            self.assertIsInstance(item, str, f"{key}: {value!r} is not a string or a flat list of them")
            self.assertIn("[[", item, f"{key}: {item!r} is not a wikilink")
        self.assertEqual([l.memory_rel("agent") for l in note_links.links(value)], want, f"{key}: {value!r}")

    def test_save_writes_every_link_key_as_wikilinks(self):
        fm = save._build_frontmatter(
            kind="fact", group="memory", slug="x", tags=[], always_load=False,
            supersedes="memory/semantic/old.md", derived_from=["memory/semantic/a.md", "memory/semantic/b.md"],
            extra={"related": "memory/semantic/new.md", "review_flags": ["near-duplicate"]},
            vault_path=self.root)
        text = fm if fm.startswith("---\n") else "---\n" + fm + "---\n"
        self.assertLinks(text, "supersedes", ["memory/semantic/old.md"])
        self.assertLinks(text, "derived_from", ["memory/semantic/a.md", "memory/semantic/b.md"])
        self.assertLinks(text, "related", ["memory/semantic/new.md"])

    def test_the_filing_engine_names_the_successor_as_a_wikilink(self):
        filing_engine._stamp_superseded(self.root, "memory/semantic/old.md", "memory/semantic/new.md")
        self.assertLinks((self.root / "memory/semantic/old.md").read_text(encoding="utf-8"),
                         "superseded_by", ["memory/semantic/new.md"])

    def test_evolve_links_the_archive_and_the_new_entry(self):
        new_path, archive_path = evolve.evolve_entry(self.root, "memory/semantic/old.md", "The new body.\n", "reworded")
        self.assertLinks(archive_path.read_text(encoding="utf-8"), "superseded_by",
                         [Path(new_path).relative_to(self.root).as_posix()])
        self.assertLinks(Path(new_path).read_text(encoding="utf-8"), "supersedes",
                         [Path(archive_path).relative_to(self.root).as_posix()])

    def test_a_merge_proposal_names_the_keeper_as_a_wikilink(self):
        p = correction.build_merge_proposal(self.root, {"members": ["memory/semantic/a.md", "memory/semantic/b.md"]})
        for _path, content in p["mutations"]:
            self.assertLinks(content, "superseded_by", ["memory/semantic/a.md"])

    def test_the_supersession_migration_writes_a_wikilink_and_keeps_a_source_version(self):
        (self.root / "memory/semantic/loser.md").write_text(
            "---\ntitle: loser\nstatus: superseded\nsupersedes: new\n---\n\nbody\n", encoding="utf-8")
        (self.root / "memory/semantic/sourced.md").write_text(
            "---\ntitle: s\nstatus: superseded\nsuperseded_by: github-issues at 2026-09-01\n---\n\nbody\n",
            encoding="utf-8")
        supersession_migrate.apply(self.root, supersession_migrate.plan(self.root), today="2026-10-26",
                                   journal=self.obs / "journal.jsonl")
        self.assertLinks((self.root / "memory/semantic/loser.md").read_text(encoding="utf-8"),
                         "superseded_by", ["memory/semantic/new.md"])
        self.assertIn("superseded_by: github-issues at 2026-09-01\n",
                      (self.root / "memory/semantic/sourced.md").read_text(encoding="utf-8"))

    def test_a_calendar_correction_links_what_it_corrects(self):
        text = calendar_facets._correction_frontmatter(date(2026, 9, 4), "meetings", date(2026, 9, 3),
                                                      datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc))
        value = _frontmatter(text)["supersedes"]
        self.assertEqual([l.target for l in note_links.links(value)], ["calendar/2026/2026-09-03-meetings"])
        self.assertIn("[[", value)


if __name__ == "__main__":
    unittest.main()
