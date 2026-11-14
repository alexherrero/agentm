#!/usr/bin/env python3
"""A note's status: the list, and the state it follows (task 204).

The rule's cases are the table the daemon's Go copy reads too
(`scripts/fixtures/status-sync-cases.json`), so the two cannot drift. The rest
holds the lint's findings, the fixer's dry run and the Python writers to the
same rule.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import lifecycle_transitions as lt  # noqa: E402
import status_words as sw  # noqa: E402
import vault_lint  # noqa: E402

TABLE = json.loads((_HERE / "fixtures" / "status-sync-cases.json").read_text(encoding="utf-8"))
LIST = ("unfiled", "draft", "queued", "active", "parked", "done", "dropped", "superseded", "merged", "archived")


def card(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\n\nBody.\n"


def status_of(text: str) -> str:
    return sw.read_state(text).get("status", "")


class TheRuleTests(unittest.TestCase):
    def test_every_case_in_the_shared_table(self):
        self.assertGreaterEqual(len(TABLE["cases"]), 10)
        for case in TABLE["cases"]:
            with self.subTest(case["name"]):
                self.assertEqual(status_of(sw.sync_status(card(*case["lines"]))), case["want"])

    def test_only_the_status_line_changes_and_a_second_sync_is_a_no_op(self):
        before = card("title: a", "type: preference", "status: active", "lifecycle: archived", "tags: [x]")
        after = sw.sync_status(before)
        self.assertEqual(after, before.replace("status: active", "status: archived"))
        self.assertEqual(sw.sync_status(after), after)

    def test_the_body_is_never_read(self):
        text = card("status: active") + "\nlifecycle: archived\n"
        self.assertEqual(sw.sync_status(text), text)


class TheWritersTests(unittest.TestCase):
    def test_moving_a_lifecycle_moves_the_status_in_the_same_write(self):
        text = card("title: a", "status: active", "lifecycle: active")
        archived = lt.set_lifecycle_text(text, "archived", since="2026-11-13")
        self.assertEqual(status_of(archived), "archived")
        revived = lt.set_lifecycle_text(archived, "active", since="2026-11-14")
        self.assertEqual(status_of(revived), "active")
        dormant = lt.set_lifecycle_text(text, "dormant", since="2026-11-13")
        self.assertEqual(status_of(dormant), "active")


class FindingTests(unittest.TestCase):
    def findings(self, rel, *lines):
        return sw.status_findings(rel, card(*lines), LIST)

    def test_a_word_on_the_list_in_agreement_with_its_state_is_clean(self):
        self.assertEqual(self.findings("agent/memory/semantic/a.md", "status: active", "lifecycle: dormant"), [])
        self.assertEqual(self.findings("projects/p/plans/old.md", "status: superseded"), [])
        self.assertEqual(self.findings("projects/p/notes.md", "title: no status"), [])

    def test_an_off_list_word_is_a_warning_with_the_nearest_word(self):
        (f,) = self.findings("projects/p/designs/d.md", "status: launched")
        self.assertEqual(f.severity, "warn")
        self.assertIn("`status: launched` is not one of", f.message)
        self.assertTrue(f.suggestion.startswith("use `status: active`"), f.suggestion)
        (g,) = self.findings("projects/p/idea.md", "status: pending-review")
        self.assertTrue(g.suggestion.startswith("use `status: unfiled`"))
        (h,) = self.findings("projects/p/x.md", "status: queud")
        self.assertTrue(h.suggestion.startswith("use `status: queued`"), h.suggestion)
        self.assertEqual(f.fix, "", "an off-list word is never fixed by the machine")

    def test_a_contradiction_is_an_error_with_its_one_fix(self):
        cases = [
            (("status: active", "lifecycle: archived"), "archived"),
            (("status: active", "consolidated_into: [\"[[lesson]]\"]"), "merged"),
            (("status: active", "lifecycle: superseded"), "superseded"),
            (("status: active", "dismissed: 2026-11-01", "type: idea"), "dropped"),
        ]
        for lines, fix in cases:
            with self.subTest(fix):
                errors = [f for f in self.findings("agent/memory/semantic/a.md", *lines) if f.severity == "error"]
                self.assertEqual([f.fix for f in errors], [fix])

    def test_an_active_note_in_an_archive_folder_is_a_contradiction(self):
        (f,) = self.findings("agent/archive/memory/semantic/a.md", "status: active")
        self.assertEqual((f.severity, f.fix), ("error", "archived"))
        # Its lifecycle already says archived: one finding, not two.
        (g,) = self.findings("agent/archive/memory/semantic/b.md", "status: active", "lifecycle: archived")
        self.assertEqual(g.fix, "archived")

    def test_a_successor_with_an_active_status_and_no_lifecycle_is_named_but_not_fixed(self):
        (f,) = self.findings("agent/memory/semantic/a.md", "status: active", "superseded_by: \"[[b]]\"")
        self.assertEqual((f.severity, f.fix), ("error", ""))

    def test_a_shape_with_its_own_words_is_not_checked(self):
        self.assertEqual(self.findings("agent/memory/crystallized/x.md", "kind: opinion-supplement",
                                       "status: proposed"), [])

    def test_with_no_list_only_contradictions_are_checked(self):
        self.assertEqual(sw.status_findings("p/a.md", card("status: launched"), ()), [])
        self.assertEqual(len(sw.status_findings("p/a.md", card("status: active", "lifecycle: archived"), ())), 1)


class VaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "vault"
        (self.root / ".obsidian").mkdir(parents=True)
        self.notes = {
            "agent/memory/semantic/merged.md": card("title: m", "status: active", "consolidated_into: [\"[[l]]\"]"),
            "agent/archive/memory/semantic/old.md": card("title: o", "status: active", "lifecycle: archived"),
            "personal/ideas/struck.md": card("type: idea", "status: active", "dismissed: 2026-10-01"),
            "projects/p/designs/d.md": card("status: launched"),
            "projects/p/plans/overtaken.md": card("status: superseded"),
            "standards/templates/t.md": card("status: a | b"),
            "agent/memory/semantic/fine.md": card("status: active", "lifecycle: active"),
        }
        for rel, text in self.notes.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_the_walk_reports_every_note_and_skips_the_templates(self):
        found = sw.vault_findings(self.root, LIST)
        by = {(rel, f.severity) for rel, f in found}
        self.assertEqual(by, {
            ("agent/memory/semantic/merged.md", "error"),
            ("agent/archive/memory/semantic/old.md", "error"),
            ("personal/ideas/struck.md", "error"),
            ("projects/p/designs/d.md", "warn"),
        })

    def test_the_fixer_lists_and_writes_only_with_apply(self):
        fixes = sw.plan_fixes(self.root)
        self.assertEqual(sorted(fixes), [
            ("agent/archive/memory/semantic/old.md", "active", "archived"),
            ("agent/memory/semantic/merged.md", "active", "merged"),
            ("personal/ideas/struck.md", "active", "dropped"),
        ])
        before = {rel: (self.root / rel).read_text(encoding="utf-8") for rel in self.notes}
        self.assertEqual(sw.main(["fix", "--vault", str(self.root)]), 0)
        self.assertEqual(before, {rel: (self.root / rel).read_text(encoding="utf-8") for rel in self.notes},
                         "a dry run wrote a note")
        self.assertEqual(sw.apply_fixes(self.root, fixes), 3)
        self.assertEqual(status_of((self.root / "personal/ideas/struck.md").read_text(encoding="utf-8")), "dropped")
        self.assertEqual(sw.plan_fixes(self.root), [])
        self.assertEqual((self.root / "projects/p/designs/d.md").read_text(encoding="utf-8"),
                         self.notes["projects/p/designs/d.md"], "an off-list word was rewritten")

    def test_the_lints_staging_folders_are_not_walked(self):
        # The lint skips these on purpose; so does the status walk.
        for rel in ("agent/memory/_opinions/served/s.md", "agent/memory/crystallized/_opinions/x.md",
                    "agent/memory/_meta/m.md", "projects/p/_idea-incubator/i/_index.md"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(card("status: served"), encoding="utf-8")
        found = {rel for rel, _f in sw.vault_findings(self.root, LIST)}
        self.assertFalse({r for r in found if "_opinions" in r or "_meta" in r or "_idea-incubator" in r}, found)
        skip = set(vault_lint._EXCLUDE_DIRS) - {"_archive", "tasks"}
        self.assertLessEqual(skip, sw._SKIP_DIRS, "a folder the lint skips is walked for status")

    def test_the_lint_carries_the_status_findings_vault_wide(self):
        memory_root = self.root / "agent"
        found = [f for f in vault_lint.status_word_findings(memory_root, LIST)]
        self.assertTrue(all(f.check_id == "status-word" for f in found))
        self.assertIn("projects/p/designs/d.md", {f.entry_path for f in found})


if __name__ == "__main__":
    unittest.main()
