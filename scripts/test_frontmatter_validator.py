#!/usr/bin/env python3
"""frontmatter_validator.py — V6-15's check-only validator (task 2).

Run directly:
    cd scripts && python3 -m unittest test_frontmatter_validator
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import frontmatter_validator as fv  # noqa: E402

_VALID_FM = (
    "---\nkind: fix\nstatus: active\ncreated: 2026-07-10\nupdated: 2026-07-10\n"
    "tags: []\ngroup: test\nslug: a\n---\n\nbody\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestValidateSingleNote(unittest.TestCase):
    def test_valid_note_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, _VALID_FM)
            self.assertEqual(fv.validate(note), [])

    def test_missing_required_field(self):
        """Each missing contract field is reported by name.

        The required set is the filing design's frontmatter block, not the shape
        `save.py` happened to emit: `tags` and `group` are no longer required (see
        the next test), and `created` is accepted as the legacy spelling of
        `captured`."""
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, "---\nkind: fix\nstatus: active\ncreated: 2026-07-10\n---\n\nbody\n")
            violations = fv.validate(note)
            self.assertTrue(any("updated" in v for v in violations))
            self.assertTrue(any("slug" in v for v in violations))
            # `created` stands in for `captured`, so that one is not reported.
            self.assertFalse(any("captured" in v for v in violations))

    def test_tags_and_group_are_no_longer_required(self):
        """Pinned so the drop is a decision rather than an omission.

        `group` was a directory pointer into the layout this rescope replaces —
        class is a directory and everything else is frontmatter, so it is a
        second, staler answer to a question the path already answers. `tags` is
        dropped from *required* because an untagged capture is ordinary, and a
        validator that refuses one only teaches the writer to emit `tags: []`."""
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, "---\nkind: fix\nstatus: active\ncaptured: 2026-07-10\n"
                         "updated: 2026-07-10\nslug: a\n---\n\nbody\n")
            self.assertEqual(fv.validate(note), [])

    def test_an_unfiled_capture_needs_no_vocabulary_yet(self):
        """Filing assigns the type, and capture never waits on a model — so a
        freshly captured note legitimately carries neither field."""
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, "---\nstatus: unfiled\ncaptured: 2026-07-10\n"
                         "updated: 2026-07-10\nslug: a\n---\n\nbody\n")
            self.assertEqual(fv.validate(note), [])

    def test_a_filed_note_without_a_vocabulary_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, "---\nstatus: active\ncaptured: 2026-07-10\n"
                         "updated: 2026-07-10\nslug: a\n---\n\nbody\n")
            violations = fv.validate(note)
            self.assertTrue(any("`type` or `kind`" in v for v in violations), violations)

    def test_unknown_kind_is_flagged_not_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, _VALID_FM.replace("kind: fix", "kind: totally-invented-kind"))
            violations = fv.validate(note)
            self.assertEqual(len(violations), 1)
            self.assertIn("not a recognized kind", violations[0])

    def test_malformed_kind_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, _VALID_FM.replace("kind: fix", "kind: handoff-artifact (verdict memo)"))
            violations = fv.validate(note)
            self.assertTrue(any("not valid kebab-case" in v for v in violations))

    def test_no_frontmatter_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, "just some body text\n")
            self.assertEqual(fv.validate(note), ["no frontmatter block found"])

    def test_validate_never_writes_to_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "a.md"
            _write(note, _VALID_FM.replace("kind: fix", "kind: bad kind"))
            before = note.read_text(encoding="utf-8")
            fv.validate(note)
            after = note.read_text(encoding="utf-8")
            self.assertEqual(before, after)


class TestValidateVault(unittest.TestCase):
    def test_missing_vault_returns_empty(self):
        self.assertEqual(fv.validate_vault("/nonexistent/path/xyz"), {})

    def test_clean_vault_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "memory" / "a.md", _VALID_FM)
            self.assertEqual(fv.validate_vault(vault), {})

    def test_dirty_vault_reports_by_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "memory" / "a.md", _VALID_FM)
            _write(vault / "desk/projects" / "p" / "b.md",
                   _VALID_FM.replace("kind: fix", "kind: made-up"))
            results = fv.validate_vault(vault)
            self.assertEqual(list(results.keys()), ["desk/projects/p/b.md"])

    def test_idea_incubator_excluded_from_default_scope(self):
        # DC-4 exemption (matches vault_lint.py's _SCOPE_DIRS["all"]):
        # _idea-incubator/ carries a bespoke frontmatter shape and must not
        # be flagged for missing the universal required fields.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "_idea-incubator" / "x" / "_index.md",
                   "---\nkind: idea-incubator\n---\n\nbody\n")
            self.assertEqual(fv.validate_vault(vault), {})

    def test_validate_vault_never_writes_to_any_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "memory" / "a.md"
            _write(note, _VALID_FM.replace("kind: fix", "kind: bad kind"))
            before = note.read_text(encoding="utf-8")
            fv.validate_vault(vault)
            after = note.read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_excludes_harness_meta_inbox_dream_staging_dirs(self):
        # DC-4 (matches vault_lint.py's _EXCLUDE_DIRS): these subdirectories
        # hold non-memory-entry content (harness state, dev-loop infra) that
        # was never meant to carry the universal frontmatter contract. A
        # real bug here flooded a real-vault run with ~1800 false
        # "no frontmatter block found" violations on plain PLAN.md/progress.md
        # harness state files nested under projects/<repo>/_harness/.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "desk/projects" / "some-repo" / "_harness" / "PLAN.md", "# Plan\n\nno frontmatter here\n")
            _write(vault / "memory" / "_meta" / "notes.md", "no frontmatter here\n")
            _write(vault / "memory" / "_inbox" / "capture.md", "no frontmatter here\n")
            _write(vault / "desk/scratch" / "proposal.md", "no frontmatter here\n")
            # Accumulate-loop supplement lanes: bespoke shape (no
            # updated/tags/group) that must not be held to the universal
            # contract — mirrors vault_lint.py's own _opinions exclusion.
            _write(vault / "memory" / "_opinions" / "plain-english" / "lane-entry.md",
                   "---\nkind: opinion-supplement\nstatus: proposed\n"
                   "created: 2026-07-25T10:00:00+00:00\nslug: lane-entry\n---\n\nbody\n")
            _write(vault / "memory" / "_opinions" / "plain-english.md",
                   "---\nkind: opinion-supplement\nstatus: promoted\n---\n\nServed.\n")
            self.assertEqual(fv.validate_vault(vault), {})

    def test_excludes_archive_dir_and_plan_archive_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "memory" / "_archive" / "old.md", "kind: bad kind\n")
            _write(vault / "memory" / "PLAN.archive.20260101-x.md", "kind: bad kind\n")
            self.assertEqual(fv.validate_vault(vault), {})


if __name__ == "__main__":
    unittest.main()
