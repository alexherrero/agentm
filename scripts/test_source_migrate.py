#!/usr/bin/env python3
"""test_source_migrate.py — one meaning for `source:` (PLAN-source-and-hygiene, task 1)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import source_migrate as sm  # noqa: E402

# The contract's own map, written out so the tests assert against a fixed
# vocabulary rather than whatever contract happens to resolve on the box.
VOCAB = {"operator-direct": "trusted", "conversation": "trusted",
         "external-fetch": "untrusted", "email": "untrusted"}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "semantic").mkdir(parents=True)
        self.journal = self.root / "source-migration.jsonl"

    def _w(self, name, fm, sub="memory/semantic"):
        p = self.vault / sub / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\n" + fm + "\n---\n\nbody\n", encoding="utf-8")
        return p

    def _entries(self):
        return [json.loads(l) for l in self.journal.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_the_four_shapes(self):
        url = self._w("fetched.md", "title: a\nkind: reference\nsource: https://example.com/page")
        ref = self._w("mined.md", "title: b\nkind: reference\nsource: idea-incubator:doom (research-complete)")
        ok = self._w("already.md", "title: c\nkind: reference\nsource: conversation")
        none = self._w("silent.md", "title: d\nkind: reference")
        rows = sm.plan(self.vault, vocabulary=VOCAB)
        by = {rel: (action, value) for rel, action, value in rows}
        self.assertEqual(by["memory/semantic/fetched.md"], (sm.FETCHED, "https://example.com/page"))
        self.assertEqual(by["memory/semantic/mined.md"], (sm.REFERENCE, "idea-incubator:doom (research-complete)"))
        self.assertNotIn("memory/semantic/already.md", by, "a transport is already the contract's shape")
        self.assertNotIn("memory/semantic/silent.md", by, "a note with no source names nothing to move")

        self.assertEqual(sm.apply(self.vault, rows, today="2026-09-06",
                                  journal=self.journal, vocabulary=VOCAB), 2)

        t = url.read_text(encoding="utf-8")
        self.assertIn("source_url: https://example.com/page\n", t)
        self.assertIn("source: external-fetch\n", t)
        self.assertIn("trust: untrusted\n", t, "the transport is derived, so the tier follows")
        self.assertTrue(t.endswith("\n\nbody\n"), "the body is untouched")

        t = ref.read_text(encoding="utf-8")
        head = t.split("\n---\n")[0]
        self.assertIn("source_id: idea-incubator:doom (research-complete)", head)
        self.assertFalse([l for l in head.splitlines() if l.startswith("source:")],
                         "no transport is invented for a note that never recorded one")
        self.assertNotIn("trust:", head, "no tier without a transport")

        self.assertIn("source: conversation", ok.read_text(encoding="utf-8"))
        self.assertNotIn("source", none.read_text(encoding="utf-8").split("\n---\n")[0])

        entries = self._entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["actor"] for e in entries}, {"migration"})
        fetched = next(e for e in entries if e["action"] == sm.FETCHED)
        self.assertEqual(fetched["was"], "https://example.com/page")
        self.assertEqual(fetched["trust"], "untrusted")
        reference = next(e for e in entries if e["action"] == sm.REFERENCE)
        self.assertIsNone(reference["source"], "the journal records that no transport was written")

        self.assertEqual(sm.plan(self.vault, vocabulary=VOCAB), [], "idempotent")

    def test_every_note_in_the_vault_is_in_scope_and_dot_dirs_are_not(self):
        self._w("outside.md", "kind: telemetry\nsource: https://example.com/a", sub="diagnostics")
        self._w("binned.md", "kind: reference\nsource: https://example.com/b", sub=".trash")
        by = {rel for rel, _, _ in sm.plan(self.vault, vocabulary=VOCAB)}
        self.assertIn("diagnostics/outside.md", by)
        self.assertNotIn(".trash/binned.md", by)

    def test_an_existing_reference_field_is_not_overwritten_by_a_second_run(self):
        p = self._w("twice.md", "kind: reference\nsource: https://example.com/x")
        rows = sm.plan(self.vault, vocabulary=VOCAB)
        sm.apply(self.vault, rows, today="2026-09-06", journal=self.journal, vocabulary=VOCAB)
        first = p.read_text(encoding="utf-8")
        self.assertEqual(sm.apply(self.vault, sm.plan(self.vault, vocabulary=VOCAB),
                                  today="2026-09-06", journal=self.journal, vocabulary=VOCAB), 0)
        self.assertEqual(p.read_text(encoding="utf-8"), first)

    def test_a_value_that_needs_quoting_is_written_quoted(self):
        # `_get` strips the quotes off whatever it reads, so a value that
        # arrived quoted has to leave quoted. The live corpus had one: a
        # provenance string holding a colon-space, which written bare turns
        # the frontmatter into a nested mapping and stops the note parsing.
        import yaml
        awkward = "opinion-supplements: good/don-t-use-no-verify (24 minings, 2026-06..2026-08)"
        p = self._w("awkward.md", f'kind: reference\nsource: "{awkward}"')
        rows = sm.plan(self.vault, vocabulary=VOCAB)
        self.assertEqual(rows, [("memory/semantic/awkward.md", sm.REFERENCE, awkward)])
        sm.apply(self.vault, rows, today="2026-09-06", journal=self.journal, vocabulary=VOCAB)
        head = p.read_text(encoding="utf-8").split("\n---\n")[0].removeprefix("---\n")
        doc = yaml.safe_load(head)
        self.assertIsInstance(doc, dict, f"the note stopped parsing:\n{head}")
        self.assertEqual(doc["source_id"], awkward, "the value must survive the round trip")

    def test_the_scalar_writer_quotes_only_what_needs_it(self):
        for value, quoted in (
            ("https://example.com/x", False),
            ("idea-incubator:doom (research-complete)", False),   # a colon with no space is fine
            ("email:<abc@example.com>", False),
            ("a: b", True),                                        # colon-space starts a mapping
            ("trailing:", True),
            ("has # a hash", True),
            (" leading space", True),
            ("- looks like a list", True),
        ):
            with self.subTest(value=value):
                self.assertEqual(sm._scalar(value).startswith('"'), quoted, sm._scalar(value))

    def test_the_cli_reports_before_it_writes(self):
        self._w("x.md", "kind: reference\nsource: https://example.com/y")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = sm.main(["--vault", str(self.vault)])
        self.assertEqual(rc, 0)
        self.assertIn("1 fetched page(s) to source_url:, 0 reference(s) to source_id:", out.getvalue())
        self.assertIn("source: https://example.com/y",
                      (self.vault / "memory" / "semantic" / "x.md").read_text(encoding="utf-8"))

    def test_no_contract_means_no_write(self):
        self._w("y.md", "kind: reference\nsource: https://example.com/z")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(sm, "transports", lambda: {}):
                rc = sm.main(["--vault", str(self.vault), "--apply"])
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write", err.getvalue())
        self.assertIn("source: https://example.com/z",
                      (self.vault / "memory" / "semantic" / "y.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
