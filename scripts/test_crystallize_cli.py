#!/usr/bin/env python3
"""Tests for crystallize.py's CLI entrypoint (Loose Ends Release 7).

The module has always described itself as "the callable an operator invokes
once an exploration is judged closed", but until this entrypoint existed it
was importable-only — so an operator could not actually invoke it. These
cover the manual path: write a digest, read it back, and fail clearly rather
than with a traceback on the two things that routinely go wrong (a malformed
digest, a slug already taken).

What these deliberately do NOT cover: distillation. The CLI takes an
already-composed digest, because turning a transcript into the five fields
is the other half of this module's `[PENDING-IMPL]` and is not built.

Run: python3 scripts/test_crystallize_cli.py
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import crystallize  # noqa: E402

_DIGEST = """## Question

Does the CLI round-trip?

## Investigation

Wrote a digest, read it back.

## Findings

It does.

## Lessons

Ship the manual path first.

## Open threads

The phase-close trigger stays deferred.
"""


def _run(argv, stdin_text=None):
    """Invoke main() capturing streams. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = crystallize.main(argv)
    finally:
        sys.stdin = old_stdin
    return rc, out.getvalue(), err.getvalue()


class TestCrystallizeCLI(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="crystallize-cli-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.vault = self.root / "vault"
        (self.vault / "personal").mkdir(parents=True)
        self.digest_file = self.root / "d.md"
        self.digest_file.write_text(_DIGEST, encoding="utf-8")

    def _write(self, slug, **kw):
        argv = ["write", "--vault-path", str(self.vault), "--slug", slug]
        if kw.get("stdin") is None:
            argv += ["--digest-file", str(self.digest_file)]
        return _run(argv, stdin_text=kw.get("stdin"))

    def test_write_then_read_round_trips(self):
        rc, out, err = self._write("round-trip")
        self.assertEqual(rc, 0, err)
        written = Path(out.strip())
        self.assertTrue(written.is_file())

        rc, out, err = _run(["read", str(written)])
        self.assertEqual(rc, 0, err)
        # Every locked section survives the trip, with its value intact.
        for title in ("Question", "Investigation", "Findings", "Lessons",
                      "Open threads"):
            self.assertIn(f"## {title}", out)
        self.assertIn("Does the CLI round-trip?", out)
        self.assertIn("The phase-close trigger stays deferred.", out)

    def test_write_accepts_stdin(self):
        rc, out, err = self._write("from-stdin", stdin=_DIGEST)
        self.assertEqual(rc, 0, err)
        self.assertTrue(Path(out.strip()).is_file())

    def test_malformed_digest_names_the_missing_section(self):
        rc, out, err = self._write("bad", stdin="## Question\n\nonly one\n")
        self.assertEqual(rc, 1)
        self.assertIn("Investigation", err, "should name what's missing")
        self.assertNotIn("Traceback", err)

    def test_empty_input_is_rejected(self):
        rc, _out, err = self._write("empty", stdin="   \n")
        self.assertEqual(rc, 1)
        self.assertIn("empty", err.lower())

    def test_slug_collision_reports_cleanly_and_preserves_the_original(self):
        rc, out, _ = self._write("taken")
        self.assertEqual(rc, 0)
        original = Path(out.strip()).read_text(encoding="utf-8")

        rc, _out, err = self._write("taken")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        self.assertNotIn("Traceback", err)
        # save_entry's never-silently-overwrite contract must hold.
        self.assertEqual(Path(out.strip()).read_text(encoding="utf-8"), original)

    def test_read_of_a_malformed_entry_fails_loudly(self):
        stray = self.vault / "personal" / "not-a-digest.md"
        stray.write_text("---\nkind: crystallized\n---\n\njust prose\n", encoding="utf-8")
        rc, _out, err = _run(["read", str(stray)])
        self.assertEqual(rc, 1)
        self.assertIn("missing locked section", err)

    def test_frontmatter_in_the_input_is_tolerated(self):
        # Lets you pipe an existing crystallized entry straight back in.
        rc, _out, err = self._write(
            "with-fm", stdin="---\nkind: crystallized\n---\n\n" + _DIGEST)
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
