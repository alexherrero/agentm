#!/usr/bin/env python3
"""Mined excerpts begin and end on word boundaries.

There was no test file for `_excerpt_around` at all, which is how it shipped
slicing at a raw character offset and produced 2,496 notes whose bodies open
part-way through a word. The operator found it by reading fifteen of them during a
labelling calibration and saying they did not look like they were supposed to be
that way.

The bar was written before the fix:

  1. No excerpt begins or ends part-way through a word.
  2. The match itself is never clipped — widening may only move outward.
  3. A long unbroken run still returns, rather than dragging the window or hanging.
  4. Text shorter than the window comes back whole, with no ellipses.
  5. The function stays deterministic and pure.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness/skills/memory/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402

# The sentence a real mined note was cut out of, near enough. The directive is
# real; the point is where the knife lands.
SENTENCE = (
    "When the gh CLI is missing the run falls back to direct push and announces "
    "the downgrade, because a completed unit of work is never hard-stopped by a "
    "missing tool and the push always goes through regardless"
)


def excerpt(text: str, needle: str, radius: int = 40) -> str:
    i = text.index(needle)
    return reflect._excerpt_around(text, i, i + len(needle), radius=radius)


class BoundaryTests(unittest.TestCase):
    """Bar 1: no excerpt begins or ends part-way through a word."""

    def assertWholeWords(self, got: str, source: str) -> None:
        """The excerpt's edges are word boundaries in the source.

        Checked against the source rather than against the excerpt's own
        characters. A first version asserted the opening character was not
        lowercase, which fails on `because` — a whole word that begins lowercase.
        "Starts a word" and "starts with a capital" are different claims, and only
        one of them is the property.
        """
        body = got
        if body.startswith("..."):
            body = body[3:]
            first = body.split(" ", 1)[0]
            self.assertTrue(
                f" {first} " in source or source.startswith(first + " "),
                f"{first!r} is not a whole word in the source: {got!r}")
        if body.endswith("..."):
            body = body[:-3]
            last = body.rsplit(" ", 1)[-1]
            self.assertTrue(
                f" {last} " in source or source.endswith(" " + last),
                f"{last!r} is not a whole word in the source: {got!r}")

    def test_the_reported_case(self):
        # `...all back to direct push` — the exact shape the operator flagged. A
        # radius landing inside "falls" used to produce it.
        got = excerpt(SENTENCE, "never hard-stopped", radius=30)
        self.assertWholeWords(got, SENTENCE)

    def test_every_radius_lands_on_a_boundary(self):
        # Swept rather than spot-checked. One radius passing says the fixture was
        # lucky; a sweep says the property holds.
        for radius in range(5, 90):
            with self.subTest(radius=radius):
                self.assertWholeWords(excerpt(SENTENCE, "direct push", radius), SENTENCE)

    def test_every_offset_lands_on_a_boundary(self):
        # And the other axis: hold the radius, move the match.
        for needle in ("gh CLI", "falls back", "downgrade", "hard-stopped",
                       "missing tool", "regardless"):
            with self.subTest(needle=needle):
                self.assertWholeWords(excerpt(SENTENCE, needle, radius=25), SENTENCE)

    def test_a_boundary_at_the_very_start_needs_no_ellipsis(self):
        got = excerpt(SENTENCE, "When the gh", radius=5)
        self.assertFalse(got.startswith("..."),
                         f"the window reaches the start; nothing was elided: {got!r}")

    def test_a_boundary_at_the_very_end_needs_no_ellipsis(self):
        got = excerpt(SENTENCE, "regardless", radius=5)
        self.assertFalse(got.endswith("..."),
                         f"the window reaches the end; nothing was elided: {got!r}")

    def test_a_window_landing_inside_the_last_word_reaches_the_end(self):
        # The mirror of the `...hen the gh CLI` case, and it needed saying
        # separately: the test above puts the window past the end, where the
        # question never arises. This one lands it a character inside "regardless",
        # where snapping forward finds no space and has to take the text's end as
        # the boundary.
        i = SENTENCE.index("goes through")
        got = reflect._excerpt_around(SENTENCE, i, i + len("goes"),
                                      radius=len("through regardles"))
        self.assertTrue(got.endswith("regardless"),
                        f"stopped inside the last word: {got!r}")
        self.assertFalse(got.endswith("..."),
                         f"claimed something was elided after the end: {got!r}")


class TheMatchSurvivesTests(unittest.TestCase):
    """Bar 2: widening may only move outward, never clip the match."""

    def test_the_matched_words_are_always_present(self):
        for radius in range(0, 60):
            with self.subTest(radius=radius):
                self.assertIn("never hard-stopped",
                              excerpt(SENTENCE, "never hard-stopped", radius),
                              "the snap ate the words the pattern fired on")

    def test_a_zero_radius_still_carries_the_match(self):
        # The degenerate case, where any inward movement at all loses everything.
        got = excerpt(SENTENCE, "downgrade", radius=0)
        self.assertIn("downgrade", got)

    def test_a_match_that_is_itself_mid_word_is_not_truncated(self):
        # A pattern can fire inside a word. Snapping must widen around it rather
        # than cut it to the boundary.
        text = "the reconfiguration step is what matters here"
        i = text.index("configuration")
        got = reflect._excerpt_around(text, i, i + len("configuration"), radius=4)
        self.assertIn("configuration", got)


class UnbrokenRunTests(unittest.TestCase):
    """Bar 3: no boundary to find is not a reason to fail."""

    def test_a_long_url_does_not_drag_the_window(self):
        url = "https://example.test/" + "a" * 400
        text = f"see {url} for the detail about deployment and rollback"
        got = excerpt(text, "deployment", radius=30)
        self.assertLess(len(got), 200,
                        "the window ran to the end looking for a space")

    def test_a_text_with_no_whitespace_at_all_still_returns(self):
        text = "a" * 300
        got = reflect._excerpt_around(text, 150, 160, radius=20)
        self.assertTrue(got, "returned nothing rather than falling back")
        self.assertIn("a", got)

    def test_the_search_is_bounded(self):
        # A run longer than the search limit falls back to the character offset,
        # which is the old behaviour and is right when there is no boundary.
        text = "start " + "z" * 200 + " end of it"
        got = reflect._excerpt_around(text, 100, 110, radius=10)
        self.assertTrue(got)


class WholeTextTests(unittest.TestCase):
    """Bar 4: nothing elided means no ellipses."""

    def test_an_elided_window_is_marked_at_both_ends(self):
        # The positive case. Every other assertion here checks that an ellipsis is
        # *absent* when nothing was elided, so dropping the marker entirely stayed
        # green — and a body that silently begins mid-sentence with no `...` is
        # worse than one that admits it.
        got = excerpt(SENTENCE, "direct push", radius=20)
        self.assertTrue(got.startswith("..."),
                        f"text was elided before the window and not marked: {got!r}")
        self.assertTrue(got.endswith("..."),
                        f"text was elided after the window and not marked: {got!r}")

    def test_a_short_text_comes_back_whole_and_unmarked(self):
        text = "always announce the downgrade"
        got = reflect._excerpt_around(text, 0, len(text), radius=80)
        self.assertEqual(got, text)
        self.assertNotIn("...", got)

    def test_newlines_are_flattened_to_one_line(self):
        # The excerpt is rendered in single-line surfaces. Stated because the
        # boundary snapping moves the window across newlines, and a snap that
        # reintroduced one would break those surfaces silently.
        text = "first line here\nsecond line there\r\nthird line yonder"
        got = excerpt(text, "second line", radius=14)
        self.assertNotIn("\n", got)
        self.assertNotIn("\r", got)
        self.assertIn("there third", got, "the newline became a space, not nothing")


class PurityTests(unittest.TestCase):
    """Bar 5: same input, same answer, and the input is untouched."""

    def test_two_calls_agree(self):
        a = excerpt(SENTENCE, "direct push", 33)
        b = excerpt(SENTENCE, "direct push", 33)
        self.assertEqual(a, b)

    def test_the_source_text_is_not_modified(self):
        before = SENTENCE
        excerpt(SENTENCE, "direct push", 33)
        self.assertEqual(SENTENCE, before)


class CorpusShapeTests(unittest.TestCase):
    """The four call sites that turn an excerpt into a note body.

    Pinned because the bug was not in `_excerpt_around` being wrong for its
    documented purpose — a ragged transparency line is harmless — but in it being
    reused where the ragged edge becomes a memory. If a fifth call site appears,
    somebody should have to look at this list.
    """

    def test_every_body_built_from_an_excerpt_is_accounted_for(self):
        src = (_SCRIPTS / "reflect.py").read_text(encoding="utf-8")
        # Lines that *start* with the kwarg, so the docstring above naming these
        # very call sites is not counted as one of them. The first version of
        # this test counted its own documentation and reported five.
        uses = [ln.strip() for ln in src.splitlines()
                if ln.strip().startswith("body=") and "excerpt" in ln]
        self.assertEqual(len(uses), 4,
                         "the set of excerpt-to-body call sites changed:\n" +
                         "\n".join(uses))


if __name__ == "__main__":
    unittest.main()
