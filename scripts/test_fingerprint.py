#!/usr/bin/env python3
"""Unit coverage for fingerprint.py + save_entry()'s auto-population
(PLAN-auto-org-dedup-and-lint, task 1).

The end-to-end half (entry_meta.fingerprint actually populated through a
real save_entry() -> upsert path) needs the sqlite-vec backend and skips
gracefully on the macOS system Python, same convention as test_vec_index.py.

Run directly:
    cd scripts && python3 -m unittest test_fingerprint
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

import fingerprint  # noqa: E402
import save  # noqa: E402
import vec_index  # noqa: E402


def _vec_backend_available(vault: Path) -> bool:
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


class TestComputeFingerprint(unittest.TestCase):
    def test_identical_bodies_match(self):
        self.assertEqual(
            fingerprint.compute_fingerprint("the same body text"),
            fingerprint.compute_fingerprint("the same body text"),
        )

    def test_whitespace_and_casing_variants_match(self):
        a = "The  Quick   Brown Fox\n\n  jumps over\t the lazy dog  \n"
        b = "the quick brown fox\njumps over the lazy dog"
        self.assertEqual(fingerprint.compute_fingerprint(a), fingerprint.compute_fingerprint(b))

    def test_crlf_line_endings_match_lf(self):
        self.assertEqual(
            fingerprint.compute_fingerprint("line one\r\nline two\r\n"),
            fingerprint.compute_fingerprint("line one\nline two\n"),
        )

    def test_genuinely_different_bodies_differ(self):
        self.assertNotEqual(
            fingerprint.compute_fingerprint("we should use sqlite for this"),
            fingerprint.compute_fingerprint("we should not use sqlite for this"),
        )

    def test_punctuation_and_link_targets_are_significant(self):
        # Conservative normalization: markdown/wikilink content is real
        # content -- two notes differing only in a link target are NOT
        # duplicates (the plan's favor-false-negative rule).
        self.assertNotEqual(
            fingerprint.compute_fingerprint("see [[note-a]] for details"),
            fingerprint.compute_fingerprint("see [[note-b]] for details"),
        )

    def test_stable_hex_shape(self):
        fp = fingerprint.compute_fingerprint("body")
        self.assertEqual(len(fp), 64)
        int(fp, 16)  # raises if not hex


class TestSaveEntryAutoPopulates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_auto_computed_fingerprint_lands_in_frontmatter(self):
        target = save.save_entry(self.vault, "reference", "auto-fp", "some body text")
        content = target.read_text(encoding="utf-8")
        expected = fingerprint.compute_fingerprint("some body text")
        self.assertIn(f"fingerprint: {expected}", content)

    def test_caller_supplied_fingerprint_always_wins(self):
        target = save.save_entry(
            self.vault, "failure-incident", "incident-fp", "trace body",
            fingerprint="semantic-join-key-123",
        )
        content = target.read_text(encoding="utf-8")
        self.assertIn("fingerprint: semantic-join-key-123", content)
        self.assertNotIn(fingerprint.compute_fingerprint("trace body"), content)

    def test_formatting_variants_produce_the_same_stored_fingerprint(self):
        t1 = save.save_entry(self.vault, "reference", "fp-variant-a", "Body   Text\n")
        t2 = save.save_entry(self.vault, "reference", "fp-variant-b", "body text")
        fp1 = next(l for l in t1.read_text(encoding="utf-8").splitlines() if l.startswith("fingerprint:"))
        fp2 = next(l for l in t2.read_text(encoding="utf-8").splitlines() if l.startswith("fingerprint:"))
        self.assertEqual(fp1, fp2)

    def test_entry_meta_fingerprint_column_populated_end_to_end(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        target = save.save_entry(self.vault, "reference", "fp-e2e", "e2e body")
        rel = str(target.relative_to(self.vault)).replace("\\", "/")
        vec_index.upsert_entry(self.vault, rel, [0.0] * vec_index.EMBEDDING_DIM)

        conn = vec_index._open_index(self.vault)
        row = conn.execute(
            "SELECT fingerprint FROM entry_meta WHERE path = ?", (rel,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], fingerprint.compute_fingerprint("e2e body"))


if __name__ == "__main__":
    unittest.main()
