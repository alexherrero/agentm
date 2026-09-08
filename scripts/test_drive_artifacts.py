#!/usr/bin/env python3
"""The sync-artifact predicate, and the emptiness it decides.

Every fixture here plants the *real* name — "Icon" plus a carriage return, the
byte sequence Google Drive actually writes — rather than the bare word. A test
that used "Icon" would pass against a predicate that never handled the carriage
return, which is the only spelling that exists on disk.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import drive_artifacts as da  # noqa: E402

# The literal Drive writes. Spelled with an escape because a real carriage
# return does not survive editors, heredocs or clipboards intact.
ICON = "Icon\r"


def plant_icon(directory: Path) -> Path:
    p = directory / ICON
    p.write_bytes(b"")
    return p


class ThePredicate(unittest.TestCase):
    def test_the_name_drive_actually_writes(self):
        self.assertTrue(da.is_artifact(ICON))

    def test_the_carriage_return_is_really_in_the_constant(self):
        # If an editor ever eats it, every fixture below silently starts
        # testing the wrong string while still passing.
        self.assertEqual(da.ICON_NAME, "Icon\r")
        self.assertTrue(da.ICON_NAME.endswith("\r"))

    def test_the_bare_word_too(self):
        # Some tools create and copy it without the carriage return.
        self.assertTrue(da.is_artifact("Icon"))

    def test_finders_artifact(self):
        self.assertTrue(da.is_artifact(".DS_Store"))

    def test_it_takes_a_path_or_a_name(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(da.is_artifact(plant_icon(Path(td))))

    def test_a_note_that_merely_starts_with_icon_is_content(self):
        # The prefix test this replaces skipped it. Somebody may legitimately
        # write about iconography.
        for name in ("Iconography.md", "Icons.md", "Icon-design.md"):
            self.assertFalse(da.is_artifact(name), name)

    def test_an_ordinary_note_is_content(self):
        self.assertFalse(da.is_artifact("a-durable-fact.md"))


class TheEmptinessItDecides(unittest.TestCase):
    """The criterion: a directory holding only `Icon\\r` is reported empty."""

    def test_a_directory_holding_only_the_icon_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "briefs"
            d.mkdir()
            plant_icon(d)
            self.assertFalse(next(d.iterdir(), None) is None,
                             "the fixture did not plant anything")
            self.assertTrue(da.is_empty(d))

    def test_a_directory_holding_only_finders_artifact_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "diagnostics"
            d.mkdir()
            (d / ".DS_Store").write_bytes(b"")
            self.assertTrue(da.is_empty(d))

    def test_one_real_note_beside_the_icon_is_not_empty(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "semantic"
            d.mkdir()
            plant_icon(d)
            (d / "a-fact.md").write_text("body", encoding="utf-8")
            self.assertFalse(da.is_empty(d))

    def test_a_missing_directory_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(da.is_empty(Path(td) / "never-existed"))

    def test_a_file_is_not_an_empty_directory(self):
        # A caller asking "can I remove this" must not be told yes about a file
        # it was only measuring.
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "note.md"
            f.write_text("body", encoding="utf-8")
            self.assertFalse(da.is_empty(f))

    def test_visible_drops_the_artifacts_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            plant_icon(d)
            (d / ".DS_Store").write_bytes(b"")
            (d / "kept.md").write_text("body", encoding="utf-8")
            self.assertEqual([p.name for p in da.visible(d.iterdir())], ["kept.md"])


class TheRemoval(unittest.TestCase):
    def test_it_removes_the_artifacts_and_reports_how_many(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            plant_icon(d)
            (d / ".DS_Store").write_bytes(b"")
            self.assertEqual(da.remove_artifacts(d), 2)
            self.assertEqual(list(d.iterdir()), [])

    def test_it_leaves_content_alone(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            plant_icon(d)
            (d / "a-fact.md").write_text("body", encoding="utf-8")
            self.assertEqual(da.remove_artifacts(d), 1)
            self.assertEqual([p.name for p in d.iterdir()], ["a-fact.md"])

    def test_the_directory_becomes_removable(self):
        # The end the cleanup actually wants: rmdir() succeeds afterwards.
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "scratch"
            d.mkdir()
            plant_icon(d)
            with self.assertRaises(OSError):
                d.rmdir()
            da.remove_artifacts(d)
            d.rmdir()
            self.assertFalse(d.exists())


class TheCleanupUsesIt(unittest.TestCase):
    """The migration's empty-directory cleanup, against the real name.

    Its predecessor found `desk/{briefs,diagnostics,projects,scratch,tasks}`
    non-empty and walked away from all five, because each still held an icon
    file.
    """

    def test_an_icon_only_directory_is_pruned(self):
        sys.path.insert(0, str(_REPO / "scripts" / "migrate"))
        import corpus_migration_3 as cm  # noqa: E402

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
            d = vault / "desk" / "briefs"
            d.mkdir(parents=True)
            plant_icon(d)

            cm._prune(vault / "desk", vault)

            self.assertFalse(d.exists(),
                             "an icon-only directory survived the cleanup")


class TheGate(unittest.TestCase):
    """The rule has one home, and the gate says so."""

    GATE = _REPO / "scripts" / "check-sync-artifacts.py"

    def _run(self, root: Path):
        return subprocess.run([sys.executable, str(self.GATE), "--root", str(root)],
                              capture_output=True, text=True)

    def test_the_repository_is_clean(self):
        r = self._run(_REPO)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_re_copied_prefix_test_fails_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "walker.py").write_text(
                'if p.name.startswith("Icon"):\n    continue\n', encoding="utf-8")
            r = self._run(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("walker.py", r.stderr)

    def test_a_re_copied_go_prefix_test_fails_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "walker.go").write_text(
                'if strings.HasPrefix(d.Name(), "Icon") { return nil }\n',
                encoding="utf-8")
            r = self._run(root)
        self.assertEqual(r.returncode, 1)

    def test_a_test_file_may_spell_the_name(self):
        # A fixture has to write the literal to plant one.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "test_walker.py").write_text(
                'if p.name.startswith("Icon"):\n    continue\n', encoding="utf-8")
            r = self._run(root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_comment_explaining_the_rule_is_not_a_copy_of_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.py").write_text(
                '# We used to test p.name.startswith("Icon") here.\n', encoding="utf-8")
            r = self._run(root)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
