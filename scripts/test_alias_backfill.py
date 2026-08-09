#!/usr/bin/env python3
"""Tests for alias_backfill.py — the write path, mostly.

This script edits thousands of notes in the operator's real vault, so what is
pinned here is the part that touches files: that an insert lands inside the
existing frontmatter and parses back, that a note which already has aliases is
never rewritten, and that a revert restores the exact bytes it started from.

The expected values are hand-written literals rather than anything recomputed
from the implementation. A check that derives its expectation from the code under
test verifies only that the code agrees with itself.
"""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alias_backfill as ab  # noqa: E402

NOTE = """\
---
kind: preferences
status: active
tags: [tokens, tooling]
slug: edit-not-write
---

User stated: use Edit not Write for existing files.
"""


class RenderTests(unittest.TestCase):
    def test_plain_form_when_every_value_is_unambiguous(self):
        self.assertEqual(
            ab.render_line(["don't rewrite whole files", "token cost of full writes"]),
            "aliases: [don't rewrite whole files, token cost of full writes]",
        )

    def test_apostrophes_survive(self):
        # The first implementation dropped these, which cost exactly the phrasing
        # the design uses as its worked example of a good alias.
        kept, rejected = ab.clean_aliases(
            ["don't rewrite whole files", "why patch instead of replacing a file"],
            ab.Candidate(path="x/y.md", flags=[], status="active"),
        )
        self.assertEqual(kept, [
            "don't rewrite whole files",
            "why patch instead of replacing a file",
        ])
        self.assertEqual(rejected, [])

    def test_quoted_form_when_a_value_needs_it(self):
        self.assertEqual(
            ab.render_line(["why is it slow?", "plain one"]),
            'aliases: ["why is it slow?", "plain one"]',
        )

    def test_every_rendered_form_parses_back_to_the_same_list(self):
        import yaml

        for aliases in (
            ["don't rewrite whole files", "a-b/c d"],
            ["why is it slow?", "50% of the cost"],
            ["a #hash here", "key: value shaped"],
            ['he said "no"', "back`tick"],
        ):
            with self.subTest(aliases=aliases):
                got = yaml.safe_load(ab.render_line(aliases))["aliases"]
                self.assertEqual(got, aliases)


class CleanTests(unittest.TestCase):
    def setUp(self):
        self.note = ab.Candidate(
            path="personal/preferences/use-edit-not-write.md",
            flags=["fragment-promoted"],
            status="active",
        )

    def test_title_permutation_is_rejected(self):
        self.note.head = "title: Use Edit not Write\n"
        kept, rejected = ab.clean_aliases(
            ["use write not edit", "don't rewrite whole files"], self.note
        )
        self.assertEqual(kept, ["don't rewrite whole files"])
        self.assertEqual(rejected, ["title-permutation:use write not edit"])

    def test_slug_permutation_is_rejected_when_the_title_differs(self):
        self.note.head = "title: Token discipline\n"
        kept, rejected = ab.clean_aliases(["not write use edit"], self.note)
        self.assertEqual(kept, [])
        self.assertEqual(rejected, ["slug-permutation:not write use edit"])

    def test_untitled_note_falls_back_to_the_slug_as_its_title(self):
        # No `title:` in frontmatter, so the filename stem is the title and a
        # permutation of it is caught by the title rule rather than the slug one.
        kept, rejected = ab.clean_aliases(["not write use edit"], self.note)
        self.assertEqual(kept, [])
        self.assertEqual(rejected, ["title-permutation:not write use edit"])

    def test_single_word_and_duplicates_go(self):
        kept, rejected = ab.clean_aliases(
            ["tokens", "token cost of writes", "Token Cost Of Writes"], self.note
        )
        self.assertEqual(kept, ["token cost of writes"])
        self.assertEqual(rejected, ["too-short:tokens", "duplicate:Token Cost Of Writes"])

    def test_leading_yaml_indicator_is_stripped_not_rejected(self):
        kept, _ = ab.clean_aliases(["- how much does a write cost"], self.note)
        self.assertEqual(kept, ["how much does a write cost"])

    def test_caps_at_six(self):
        kept, _ = ab.clean_aliases([f"phrase number {i}" for i in range(9)], self.note)
        self.assertEqual(len(kept), 6)


class InsertTests(unittest.TestCase):
    def test_alias_line_lands_as_the_last_frontmatter_key(self):
        out = ab.insert_aliases(NOTE, ["don't rewrite whole files", "cost of a write"])
        self.assertEqual(
            out,
            "---\n"
            "kind: preferences\n"
            "status: active\n"
            "tags: [tokens, tooling]\n"
            "slug: edit-not-write\n"
            "aliases: [don't rewrite whole files, cost of a write]\n"
            "---\n"
            "\n"
            "User stated: use Edit not Write for existing files.\n",
        )

    def test_body_is_untouched(self):
        out = ab.insert_aliases(NOTE, ["one two", "three four"])
        self.assertEqual(out.split("---\n", 2)[2], NOTE.split("---\n", 2)[2])

    def test_a_blank_line_inside_the_frontmatter_is_left_where_it_was(self):
        # Tidying it away would be a byte revert could not put back.
        odd = "---\nkind: x\n\n---\nbody\n"
        out = ab.insert_aliases(odd, ["one two", "three four"])
        self.assertEqual(
            out, "---\nkind: x\n\naliases: [one two, three four]\n---\nbody\n"
        )
        self.assertEqual(out.replace("\naliases: [one two, three four]", "", 1), odd)

    def test_refuses_a_note_that_already_has_aliases(self):
        already = NOTE.replace("slug: edit-not-write\n", "aliases: [already here]\n")
        with self.assertRaises(ValueError):
            ab.insert_aliases(already, ["one two"])

    def test_refuses_a_note_with_no_frontmatter(self):
        with self.assertRaises(ValueError):
            ab.insert_aliases("# just a body\n", ["one two"])

    def test_verify_catches_a_corrupted_write(self):
        with self.assertRaises(ValueError):
            ab.verify_written("---\nkind: x\n---\nbody\n", ["one two"])


class CreateFrontmatterTests(unittest.TestCase):
    BARE = "# Progress — blog\n\n2026-06-06 /plan — created plan.\n"

    def test_block_holds_the_aliases_and_nothing_else(self):
        out = ab.create_frontmatter_block(self.BARE, ["one two", "three four"])
        self.assertEqual(
            out, "---\naliases: [one two, three four]\n---\n\n" + self.BARE
        )

    def test_no_status_or_type_is_guessed(self):
        # Writing `status: unfiled` here would demote several hundred real
        # documents into a penalty class to make the block look complete.
        out = ab.create_frontmatter_block(self.BARE, ["one two", "three four"])
        head = ab.FRONTMATTER_RE.match(out).group(1)
        self.assertEqual(head, "aliases: [one two, three four]")

    def test_body_is_untouched(self):
        out = ab.create_frontmatter_block(self.BARE, ["one two", "three four"])
        self.assertTrue(out.endswith(self.BARE))

    def test_refuses_a_note_that_already_has_frontmatter(self):
        with self.assertRaises(ValueError):
            ab.create_frontmatter_block(NOTE, ["one two"])

    def test_created_prefix_is_exactly_what_was_prepended(self):
        aliases = ["one two", "three four"]
        out = ab.create_frontmatter_block(self.BARE, aliases)
        self.assertEqual(out[len(ab.created_prefix(aliases)):], self.BARE)


class ArtifactRuleTests(unittest.TestCase):
    def test_machine_exhaust_is_named_and_excluded(self):
        for rel, reason in (
            ("_moc/convention.md", "regenerated link index"),
            ("_meta/skill-discovery-cache/x/diff-2026-06-15.md", "raw upstream README diff"),
            ("_dream-staging/abc/digest.md", "dream-staging digest"),
            ("_meta/archive/vault-lint-2026-05-29.md", "machine lint report"),
            ("_meta/vault-lint-2026-07-15.md", "machine lint report"),
        ):
            with self.subTest(rel=rel):
                self.assertEqual(ab.frontmatter_is_worth_creating(rel), reason)

    def test_real_documents_are_not_excluded(self):
        for rel in (
            "projects/agentm/_harness/archive/v5/PLAN.archive.20260618.md",
            "projects/blog/_harness/progress.md",
            "_vault-archive/ag-design-history/memory-os-architecture.md",
            "personal/trusted-sources.md",
            "_meta/how-to-use-agentmemory.md",
        ):
            with self.subTest(rel=rel):
                self.assertIsNone(ab.frontmatter_is_worth_creating(rel))


class ExistingAliasTests(unittest.TestCase):
    def test_inline_list_counts_as_present(self):
        self.assertEqual(ab.existing_aliases("aliases: [a, b]\n"), "[a, b]")

    def test_block_list_counts_as_present(self):
        self.assertEqual(ab.existing_aliases("aliases:\n  - a\n  - b\n"), "a, b")

    def test_empty_placeholder_reads_as_absent(self):
        self.assertIsNone(ab.existing_aliases("aliases: []\ntags: [x]\n"))
        self.assertIsNone(ab.existing_aliases("aliases:\ntags: [x]\n"))

    def test_no_key_reads_as_absent(self):
        self.assertIsNone(ab.existing_aliases("tags: [x]\n"))


class RevertTests(unittest.TestCase):
    """Revert has to restore the original bytes, and refuse anything else."""

    def _run(self, mutate=None, op="insert", original=NOTE):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp, "vault")
            (vault / "personal").mkdir(parents=True)
            note = vault / "personal" / "n.md"
            aliases = ["don't rewrite whole files", "cost of a write"]
            after = (
                ab.create_frontmatter_block(original, aliases)
                if op == "create"
                else ab.insert_aliases(original, aliases)
            )
            note.write_text(after, encoding="utf-8")
            journal = Path(tmp, "j.jsonl")
            journal.write_text(
                json.dumps(
                    {
                        "path": "personal/n.md",
                        "outcome": "aliased",
                        "op": op,
                        "aliases": aliases,
                        "sha_before": hashlib.sha256(original.encode()).hexdigest(),
                        "sha_after": hashlib.sha256(after.encode()).hexdigest(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            if mutate:
                note.write_text(mutate(note.read_text(encoding="utf-8")), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                ab.main(["--vault", str(vault), "revert", "--journal", str(journal)])
            return note.read_text(encoding="utf-8")

    def test_restores_the_original_bytes(self):
        self.assertEqual(self._run(), NOTE)

    def test_removes_a_whole_created_block(self):
        bare = "# Progress — blog\n\n2026-06-06 /plan — created plan.\n"
        self.assertEqual(self._run(op="create", original=bare), bare)

    def test_leaves_a_note_edited_since_alone(self):
        edited = self._run(mutate=lambda t: t + "\nsomeone else wrote this\n")
        self.assertIn("someone else wrote this", edited)
        self.assertIn("aliases: [", edited)


class ReapplyTests(unittest.TestCase):
    """Reapply is the other half of the undo, and it has to be exact.

    The week-3 retest reverted 1,930 aliases on evidence, and evidence can move
    again. Re-running the generator is not an undo — it calls a model and writes
    *different* aliases — so the round trip has to come from the journal, and it
    has to land on the same bytes or the corpus can no longer be measured twice
    against the same baseline.
    """

    def _round_trip(self, op="insert", original=NOTE, mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp, "vault")
            (vault / "personal").mkdir(parents=True)
            note = vault / "personal" / "n.md"
            aliases = ["don't rewrite whole files", "cost of a write"]
            after = (
                ab.create_frontmatter_block(original, aliases)
                if op == "create"
                else ab.insert_aliases(original, aliases)
            )
            note.write_text(after, encoding="utf-8")
            journal = Path(tmp, "j.jsonl")
            journal.write_text(
                json.dumps({
                    "path": "personal/n.md", "outcome": "aliased", "op": op,
                    "aliases": aliases,
                    "sha_before": hashlib.sha256(original.encode()).hexdigest(),
                    "sha_after": hashlib.sha256(after.encode()).hexdigest(),
                }) + "\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                ab.main(["--vault", str(vault), "revert", "--journal", str(journal)])
            reverted = note.read_text(encoding="utf-8")
            if mutate:
                note.write_text(mutate(reverted), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                ab.main(["--vault", str(vault), "reapply", "--journal", str(journal)])
            return reverted, note.read_text(encoding="utf-8"), after

    def test_revert_then_reapply_returns_the_exact_bytes(self):
        reverted, restored, after = self._round_trip()
        self.assertEqual(reverted, NOTE)
        self.assertEqual(restored, after)

    def test_round_trip_survives_a_created_block(self):
        bare = "# Progress — blog\n\n2026-06-06 /plan — created plan.\n"
        reverted, restored, after = self._round_trip(op="create", original=bare)
        self.assertEqual(reverted, bare)
        self.assertEqual(restored, after)

    def test_leaves_a_note_edited_since_the_revert_alone(self):
        """Symmetric with revert's own refusal. A note someone touched between
        the revert and the reapply is not a note this can restore blind."""
        _, restored, _ = self._round_trip(
            mutate=lambda t: t + "\nsomeone else wrote this\n")
        self.assertIn("someone else wrote this", restored)
        self.assertNotIn("aliases: [", restored)

    def test_reapply_is_idempotent(self):
        """Running it twice must not write a second aliases line."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp, "vault")
            (vault / "personal").mkdir(parents=True)
            note = vault / "personal" / "n.md"
            aliases = ["cost of a write"]
            after = ab.insert_aliases(NOTE, aliases)
            note.write_text(after, encoding="utf-8")
            journal = Path(tmp, "j.jsonl")
            journal.write_text(
                json.dumps({
                    "path": "personal/n.md", "outcome": "aliased", "op": "insert",
                    "aliases": aliases,
                    "sha_before": hashlib.sha256(NOTE.encode()).hexdigest(),
                    "sha_after": hashlib.sha256(after.encode()).hexdigest(),
                }) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                ab.main(["--vault", str(vault), "revert", "--journal", str(journal)])
                ab.main(["--vault", str(vault), "reapply", "--journal", str(journal)])
                ab.main(["--vault", str(vault), "reapply", "--journal", str(journal)])
            text = note.read_text(encoding="utf-8")
            self.assertEqual(text, after)
            self.assertEqual(text.count("aliases: ["), 1)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp, "vault")
            (vault / "personal").mkdir(parents=True)
            note = vault / "personal" / "n.md"
            aliases = ["cost of a write"]
            after = ab.insert_aliases(NOTE, aliases)
            note.write_text(after, encoding="utf-8")
            journal = Path(tmp, "j.jsonl")
            journal.write_text(
                json.dumps({
                    "path": "personal/n.md", "outcome": "aliased", "op": "insert",
                    "aliases": aliases,
                    "sha_before": hashlib.sha256(NOTE.encode()).hexdigest(),
                    "sha_after": hashlib.sha256(after.encode()).hexdigest(),
                }) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                ab.main(["--vault", str(vault), "revert", "--journal", str(journal)])
                ab.main(["--vault", str(vault), "reapply", "--journal", str(journal),
                         "--dry-run"])
            self.assertEqual(note.read_text(encoding="utf-8"), NOTE)


if __name__ == "__main__":
    unittest.main()
