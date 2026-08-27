#!/usr/bin/env python3
"""Backfilling the reference bodies that were never captured.

The bar, written before the pass ran on anything:

  1. A thin note gains the README and keeps its title, its source line and every
     frontmatter field. A settled note's slug never changes; links point at it.
  2. A note that already has a summary is not touched.
  3. No README, or one no better than what is there, leaves the note alone and
     says so. Replacing a tagline with a shorter tagline is churn.
  4. A note that is not a GitHub repo is reported, not guessed at.
  5. Every write goes through the revert log and comes back byte-identical.
  6. Nothing here reaches the network.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness/skills/memory/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import backfill_reference_bodies as bb  # noqa: E402
from revert_log import RevertLog  # noqa: E402

README = ("DeepSeek-OCR: Contexts Optical Compression. Explore the boundaries of "
          "visual-text compression, trading a little fidelity for a large "
          "reduction in the context a document costs to read.")


class Case(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.vault = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        logs = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        locks = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.log = RevertLog(self.vault, log_root=logs, lock_root=locks)

    def note(self, rel, *, title="DeepSeek-OCR", prose="",
             url="https://github.com/deepseek-ai/DeepSeek-OCR",
             status="active", extra=""):
        parts = [f"# {title}"]
        if prose:
            parts.append(prose)
        parts.append(f"Source: {url}")
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f"---\ntype: reference\nstatus: {status}\nslug: deepseek-ocr\n"
            f"source: {url}\n{extra}---\n\n" + "\n\n".join(parts) + "\n",
            encoding="utf-8")
        return p

    def scan(self, readme=README, **kw):
        return bb.scan(self.vault, fetch=lambda repo: readme, **kw)


class BackfillTests(Case):
    """Bar 1."""

    def test_a_pointer_gains_the_readme(self):
        self.note("m/a.md")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "backfilled")
        self.assertIn("visual-text compression", f.new_body)

    def test_a_tagline_is_replaced_by_the_readme(self):
        self.note("m/a.md", prose="Contexts Optical Compression")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "backfilled")
        self.assertEqual(f.words_before, 3)
        self.assertGreater(f.words_after, 20)

    def test_the_title_and_source_survive(self):
        p = self.note("m/a.md")
        bb.apply(self.vault, self.scan(), self.log, "run-1")
        after = p.read_text(encoding="utf-8")
        self.assertIn("# DeepSeek-OCR", after)
        self.assertIn("Source: https://github.com/deepseek-ai/DeepSeek-OCR", after)

    def test_the_frontmatter_survives_untouched(self):
        # The slug especially. A settled note's slug never changes, because
        # links point at it, and this is a content backfill rather than a
        # re-filing.
        p = self.note("m/a.md", extra="tags: [skill-discovery, web]\n"
                                      "rubric_score: 1\n")
        before = p.read_text(encoding="utf-8").split("---")[1]
        bb.apply(self.vault, self.scan(), self.log, "run-1")
        after = p.read_text(encoding="utf-8").split("---")[1]
        self.assertEqual(after, before, "the frontmatter moved")

    def test_the_source_line_is_not_duplicated(self):
        p = self.note("m/a.md")
        bb.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertEqual(p.read_text(encoding="utf-8").count("Source:"), 1)


class LeaveAloneTests(Case):
    """Bars 2, 3 and 4."""

    def test_a_note_with_a_summary_is_not_a_finding(self):
        self.note("m/a.md", prose=" ".join(["word"] * 40))
        self.assertEqual(self.scan().findings, [],
                         "a note that already reads well was queued for rewrite")

    def test_no_readme_leaves_it_alone_and_says_so(self):
        p = self.note("m/a.md", prose="Contexts Optical Compression")
        before = p.read_bytes()
        f = self.scan(readme="").findings[0]
        self.assertEqual(f.outcome, "no-readme")
        bb.apply(self.vault, self.scan(readme=""), self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_a_shorter_readme_is_not_an_improvement(self):
        # Replacing a nine-word tagline with a four-word one is churn dressed as
        # a repair, and it would run again on the next pass.
        p = self.note("m/a.md", prose="one two three four five six seven eight nine")
        before = p.read_bytes()
        f = self.scan(readme="four words only here").findings[0]
        self.assertEqual(f.outcome, "no-readme")
        bb.apply(self.vault, self.scan(readme="four words only here"),
                 self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_a_non_repo_source_is_reported_not_guessed(self):
        self.note("m/a.md", url="https://www.anthropic.com/research/some-post")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "not-a-repo")

    def test_an_expired_note_is_skipped(self):
        self.note("m/a.md", status="expired")
        self.assertEqual(self.scan().findings, [])

    def test_a_note_that_is_not_a_reference_is_skipped(self):
        p = self.vault / "m/idea.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntype: idea\nstatus: active\n---\n\n# A thing\n\n"
                     "Source: https://github.com/a/b\n", encoding="utf-8")
        self.assertEqual(self.scan().findings, [])


class RepoParsingTests(Case):
    def test_the_repo_comes_off_the_frontmatter_source(self):
        raw = ("---\ntype: reference\nsource: https://github.com/deepseek-ai/DeepSeek-OCR\n"
               "---\n\n# T\n")
        self.assertEqual(bb.repo_of(raw), "deepseek-ai/DeepSeek-OCR")

    def test_the_source_line_is_the_fallback(self):
        raw = "---\ntype: reference\n---\n\n# T\n\nSource: https://github.com/a/b\n"
        self.assertEqual(bb.repo_of(raw), "a/b")

    def test_a_git_suffix_is_dropped(self):
        raw = "---\nsource: https://github.com/a/b.git\n---\n\n# T\n"
        self.assertEqual(bb.repo_of(raw), "a/b")

    def test_a_deep_github_url_is_not_a_repo_root(self):
        # An issue or a file URL names a repo, and treating it as one would fetch
        # a README for a note that is about something inside it.
        raw = "---\nsource: https://github.com/a/b/issues/12\n---\n\n# T\n"
        self.assertEqual(bb.repo_of(raw), "a/b")

    def test_a_non_github_url_is_not_a_repo(self):
        for url in ("https://example.test/x", "https://gitlab.com/a/b", ""):
            raw = f"---\nsource: {url}\n---\n\n# T\n"
            self.assertEqual(bb.repo_of(raw), "", url)


class RevertTests(Case):
    """Bar 5."""

    def test_the_run_reverts_byte_identically(self):
        paths = [self.note("m/a.md"), self.note("m/b.md", title="Other")]
        awkward = self.vault / "m/awkward.md"
        awkward.write_bytes(
            b"---\r\ntype: reference\r\nstatus: active\r\n"
            b"source: https://github.com/a/b\r\n---\r\n\r\n# A\r\n\r\n"
            b"Source: https://github.com/a/b")
        paths.append(awkward)
        before = {p: p.read_bytes() for p in paths}

        entry = bb.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertTrue(any(p.read_bytes() != before[p] for p in paths))
        self.log.revert("run-1", entry)
        for p in paths:
            self.assertEqual(p.read_bytes(), before[p], str(p))

    def test_a_second_run_writes_nothing(self):
        self.note("m/a.md")
        bb.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertEqual(bb.apply(self.vault, self.scan(), self.log, "run-2"), "",
                         "the backfill wants to rewrite what it just wrote")


class BatchTests(Case):
    def test_the_cap_bounds_one_run(self):
        for i in range(10):
            self.note(f"m/n{i}.md")
        bb.apply(self.vault, self.scan(), self.log, "run-1", batch=3)
        done = sum(1 for i in range(10)
                   if "visual-text" in (self.vault / f"m/n{i}.md").read_text(
                       encoding="utf-8"))
        self.assertEqual(done, 3)

    def test_the_cap_matches_the_other_passes(self):
        import dream_confirm
        self.assertEqual(bb.DEFAULT_BATCH,
                         dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP)

    def test_scanning_writes_nothing(self):
        p = self.note("m/a.md")
        before = p.read_bytes()
        self.scan()
        self.assertEqual(p.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
