#!/usr/bin/env python3
"""The collapse migration, against a scratch corpus.

Three properties carry it, and each has cost something somewhere before.

**Nothing moves.** The migration edits one frontmatter line. A path-set diff
before and after is what makes that a measured claim rather than an assurance —
and it is the claim every link in the vault depends on.

**Idempotent.** A second run finds nothing to do. A migration that is not
idempotent cannot be resumed after an interruption, and a ten-thousand-note pass
will be interrupted eventually.

**Line-surgical.** The frontmatter block is never parsed and re-serialized. The
first draft of the rewrite produced `type:preferencespreference` from an inverted
whitespace slice — caught by a dry run, which is why the dry run exists, and why
the exact before/after of a line is pinned here rather than only its effect.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import migrate_type_collapse as migrate  # noqa: E402
import storage_rules  # noqa: E402

_BUILD_DIR = None


# What `storage_rules` pointed at before this module touched it, so the
# teardown restores a real value rather than a guess.
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def setUpModule() -> None:
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest("go is not on this machine; set $AGENTMD to a built binary")
    _BUILD_DIR = tempfile.TemporaryDirectory(prefix="agentmd-build-")
    binary = Path(_BUILD_DIR.name) / "agentmd"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/agentmd"],
                   cwd=_REPO / "daemon", check=True, capture_output=True)
    os.environ["AGENTMD"] = str(binary)
    storage_rules.DAEMON_BIN = str(binary)
    storage_rules._CACHE = None


def tearDownModule() -> None:
    """Undo everything setUpModule did, not just the directory.

    Deleting the build directory while leaving `$AGENTMD` pointing into it is
    what made a full `unittest discover` run fail: every later module takes its
    own `if os.environ.get("AGENTMD"): return` early exit, then shells out to a
    binary that is no longer there.

    Only what this module set. A module that inherited `$AGENTMD` from the
    environment returned early and built nothing, so the variable is not its to
    clear.
    """
    global _BUILD_DIR
    if _BUILD_DIR is None:
        return
    _BUILD_DIR.cleanup()
    _BUILD_DIR = None
    os.environ.pop("AGENTMD", None)
    storage_rules.DAEMON_BIN = _ORIGINAL_DAEMON_BIN
    storage_rules._CACHE = None


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)
        storage_rules._CACHE = None
        self.addCleanup(setattr, storage_rules, "_CACHE", None)
        self.rules = storage_rules.load()

    def note(self, rel: str, frontmatter: str, body: str = "Body text.\n") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
        return path

    def plan(self):
        return migrate.walk(self.vault, self.rules)

    def run_migration(self):
        notes, problems, scanned = self.plan()
        for n in notes:
            migrate.apply_note(n)
        return notes, problems, scanned


class RewriteTests(_Base):
    def test_a_retired_value_takes_its_replacement(self) -> None:
        p = self.note("memory/preferences/a.md", "kind: preferences\nstatus: active\n")
        self.run_migration()
        self.assertIn("type: preference\n", p.read_text(encoding="utf-8"))
        self.assertNotIn("kind:", p.read_text(encoding="utf-8"))

    def test_a_current_memory_type_only_changes_field(self) -> None:
        p = self.note("memory/workflow/a.md", "kind: workflow\nstatus: active\n")
        self.run_migration()
        self.assertIn("type: workflow\n", p.read_text(encoding="utf-8"))

    def test_a_record_kind_is_left_alone(self) -> None:
        p = self.note("memory/briefs/a.md", "kind: brief\nstatus: active\n")
        before = p.read_text(encoding="utf-8")
        notes, _, _ = self.run_migration()
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertEqual([n for n in notes if n.rel.endswith("briefs/a.md")], [])

    def test_the_exact_line_is_what_changes(self) -> None:
        """Pinned as a literal, because the bug this catches produced a line that
        parsed as YAML and said the wrong thing."""
        self.note("memory/preferences/a.md", "kind: preferences\nstatus: active\n")
        notes, _, _ = self.plan()
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(notes[0].edits), 1)
        self.assertEqual(notes[0].edits[0].old_line, "kind: preferences")
        self.assertEqual(notes[0].edits[0].new_line, "type: preference")

    def test_nothing_but_the_one_line_is_rewritten(self) -> None:
        p = self.note(
            "memory/preferences/a.md",
            "kind: preferences\nstatus: active\ntags: [a, b]\nslug: a\n",
            body="A body with `kind: preferences` written in it on purpose.\n")
        before = p.read_text(encoding="utf-8").split("\n")
        self.run_migration()
        after = p.read_text(encoding="utf-8").split("\n")
        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(differing, [1], "more than the vocabulary line changed")
        self.assertIn("A body with `kind: preferences` written in it", "\n".join(after))

    def test_a_note_with_no_frontmatter_is_untouched(self) -> None:
        p = self.vault / "memory" / "plain.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Just a heading\n\nkind: preferences\n", encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        self.run_migration()
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_a_note_carrying_both_fields_is_reported_not_guessed(self) -> None:
        self.note("memory/x/a.md", "type: workflow\nkind: brief\nstatus: active\n")
        notes, problems, _ = self.plan()
        self.assertEqual(notes, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("both", problems[0][1])

    def test_an_unplaceable_value_is_reported_not_guessed(self) -> None:
        self.note("memory/x/a.md", "kind: something-invented\nstatus: active\n")
        notes, problems, _ = self.plan()
        self.assertEqual(notes, [])
        self.assertIn("neither register", problems[0][1])


class StatusTests(_Base):
    """The corpus carries four statuses the contract does not define, and a note
    whose status no pass can reason about is a note no pass will touch."""

    def test_every_legacy_status_maps(self) -> None:
        cases = {
            "inbox": "unfiled",
            "promoted": "active",
            "parked": "unfiled",
            "evergreen": "active",
        }
        for old, want in cases.items():
            p = self.note(f"memory/x/{old}.md", f"kind: workflow\nstatus: {old}\n")
            self.run_migration()
            self.assertIn(f"status: {want}\n", p.read_text(encoding="utf-8"),
                          f"{old} did not map to {want}")

    def test_the_four_contract_statuses_are_left_alone(self) -> None:
        for status in ("unfiled", "active", "superseded", "expired"):
            p = self.note(f"memory/y/{status}.md", f"type: workflow\nstatus: {status}\n")
            before = p.read_text(encoding="utf-8")
            self.run_migration()
            self.assertEqual(p.read_text(encoding="utf-8"), before,
                             f"{status} was rewritten and should not have been")

    def test_an_unknown_status_is_left_alone_rather_than_guessed(self) -> None:
        """A status nothing recognizes is not the same as one this pass knows how
        to map. Guessing at it would be inventing lifecycle state."""
        p = self.note("memory/x/a.md", "type: workflow\nstatus: something-else\n")
        before = p.read_text(encoding="utf-8")
        self.run_migration()
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_both_lines_change_in_one_pass(self) -> None:
        p = self.note("memory/x/a.md",
                      "kind: preferences\nstatus: inbox\nslug: a\n")
        self.run_migration()
        text = p.read_text(encoding="utf-8")
        self.assertIn("type: preference\n", text)
        self.assertIn("status: unfiled\n", text)
        self.assertNotIn("kind:", text)
        self.assertNotIn("status: inbox", text)

    def test_a_second_run_over_both_fields_finds_nothing(self) -> None:
        self.note("memory/x/a.md", "kind: preferences\nstatus: inbox\n")
        self.run_migration()
        notes, problems, _ = self.plan()
        self.assertEqual(notes, [])
        self.assertEqual(problems, [])


class IdempotencyTests(_Base):
    def test_a_second_run_finds_nothing(self) -> None:
        self.note("memory/preferences/a.md", "kind: preferences\nstatus: active\n")
        self.note("memory/workflow/b.md", "kind: workflow\nstatus: active\n")
        first, _, _ = self.run_migration()
        self.assertEqual(len(first), 2)
        second, problems, _ = self.plan()
        self.assertEqual(second, [], "the migration is not idempotent")
        self.assertEqual(problems, [])

    def test_a_second_run_changes_no_bytes(self) -> None:
        p = self.note("memory/preferences/a.md", "kind: preferences\nstatus: active\n")
        self.run_migration()
        after_first = p.read_bytes()
        self.run_migration()
        self.assertEqual(p.read_bytes(), after_first)


class NothingMovesTests(_Base):
    def test_the_path_set_is_identical_before_and_after(self) -> None:
        """The claim every link in the vault depends on."""
        for i in range(12):
            self.note(f"memory/preferences/note-{i:02d}.md",
                      "kind: preferences\nstatus: active\n")
        self.note("memory/workflow/w.md", "kind: workflow-pattern\nstatus: active\n")
        before = {str(p.relative_to(self.vault)) for p in self.vault.rglob("*.md")}
        self.run_migration()
        after = {str(p.relative_to(self.vault)) for p in self.vault.rglob("*.md")}
        self.assertEqual(before, after)

    def test_a_retyped_note_keeps_its_directory(self) -> None:
        """`memory/preferences/` keeps holding a note whose type is now
        `preference`. The directory name stops matching the value on purpose:
        under the contract a directory encodes class, not type, and moving the
        file to make the two agree is the one thing the contract forbids."""
        p = self.note("memory/preferences/a.md", "kind: preferences\nstatus: active\n")
        self.run_migration()
        self.assertTrue(p.is_file())
        self.assertIn("/preferences/", str(p).replace("\\", "/"))


class ScopeTests(_Base):
    def test_inbox_is_now_in_scope(self) -> None:
        """The deferral, redeemed.

        `_inbox` was excluded from the vocabulary-only pass on the reasoning that
        migrating it then meant rewriting the same note twice — once for its type
        and again for its status. That reasoning only holds if the two rewrites
        eventually combine, and this is the pass where they do. A note in there
        needs both lines, and gets both in one edit."""
        self.note("memory/_inbox/a.md", "kind: preferences\nstatus: inbox\n")
        notes, problems, _ = self.plan()
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 1)
        labels = sorted(e.label for e in notes[0].edits)
        self.assertEqual(labels, [
            "kind: preferences  ->  type: preference",
            "status: inbox  ->  status: unfiled",
        ])

    def test_a_note_needing_only_a_status_change_is_planned(self) -> None:
        self.note("memory/x/a.md", "kind: brief\nstatus: promoted\n")
        notes, _, _ = self.plan()
        self.assertEqual(len(notes), 1)
        self.assertEqual([e.label for e in notes[0].edits],
                         ["status: promoted  ->  status: active"])

    def test_harness_state_is_out_of_scope(self) -> None:
        self.note("desk/projects/x/_harness/PLAN.md", "kind: preferences\n")
        notes, _, _ = self.plan()
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
