#!/usr/bin/env python3
"""Tests for recall.py's temporal-bound extractor (task 5.5, hybrid-retrieval
plan — "temporal wiring, re-scoped as non-regression").

The mechanism this pins: `_extract_temporal_bound` deterministically resolves
a natural-language prompt's date phrase ("last week", "in June", "yesterday",
"since March", "in 2026") into `-after`/`-before` bounds for the daemon's
`agentmd search` — bounds that already existed on `index.Query` but that no
caller had ever set automatically. It is deliberately narrow: a date bound is
a filter, not a retrieval channel, so a wrong bound is strictly worse than
none, and this function is built to abstain rather than guess.

Three things here are easy to get wrong, and each has its own test group:

  1. **"When did I decide X" bounds nothing; "what did I decide last week"
     bounds something.** The plan's own worked example. Every gold-set
     episodic-temporal question is the first shape — an open question asking
     FOR a date, not a phrase supplying one to filter with. Getting this
     backwards would bound a query that cannot be bounded and silently
     suppress its own answer.

  2. **A trigger word must be adjacent to the date token, not merely present
     somewhere in the sentence.** "in June" bounds; "the June release" does
     not, because no "in"/"since" immediately precedes "June". Getting this
     wrong turns every topic reference that happens to name a month into a
     capture-date filter.

  3. **"since <phrase>" is open-ended; the bare phrase alone is closed.**
     "last week" is a literal substring of "since last week", so the wrong
     match order hands back a closed range where "since" specifically means
     "onward, no upper bound".

**Expected values are hand-derived from the calendar, never recomputed with
the implementation's own helpers** (`_add_months`, `_resolve_month_year`) —
a test that asks the code under test what the code under test would answer
proves only that they agree.

The reference instant is always injected (`now=`), so none of this depends on
the day the suite happens to run.

Run: python3 scripts/test_recall_temporal.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import recall  # noqa: E402

# Friday, 2026-08-14 — an ordinary mid-week, mid-month, mid-year instant with
# no boundary coincidence (not the 1st of a month, not a Monday, not Jan 1),
# so a test that passes here is not accidentally passing only because `now`
# sat on an edge.
_NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


class NamedExamplePhrasesTests(unittest.TestCase):
    """The five phrase shapes task 5.5's own text names explicitly."""

    def test_last_week_bounds_the_previous_iso_week(self):
        # 2026-08-14 is a Friday; that week's Monday is 2026-08-10, so last
        # week is the Monday before: 2026-08-03 through (exclusive) 08-10.
        self.assertEqual(
            recall._extract_temporal_bound("what did I decide last week", now=_NOW),
            ("2026-08-03", "2026-08-10"),
        )

    def test_in_month_bounds_that_single_month(self):
        self.assertEqual(
            recall._extract_temporal_bound("what happened in June", now=_NOW),
            ("2026-06-01", "2026-07-01"),
        )

    def test_yesterday_bounds_a_single_day(self):
        self.assertEqual(
            recall._extract_temporal_bound("what did we discuss yesterday", now=_NOW),
            ("2026-08-13", "2026-08-14"),
        )

    def test_since_month_is_open_ended(self):
        self.assertEqual(
            recall._extract_temporal_bound("notes since March", now=_NOW),
            ("2026-03-01", ""),
        )

    def test_in_year_bounds_that_single_calendar_year(self):
        self.assertEqual(
            recall._extract_temporal_bound("plans in 2026", now=_NOW),
            ("2026-01-01", "2027-01-01"),
        )


class AbstentionTests(unittest.TestCase):
    """A phrase that cannot be resolved with confidence must produce no bound
    at all — abstention is the load-bearing behavior this task exists to get
    right, not an edge case of it.
    """

    def test_when_did_i_decide_x_bounds_nothing(self):
        """The plan's own worked example, abstain half."""
        self.assertIsNone(
            recall._extract_temporal_bound("When did I decide to begin my first AgentM arc?", now=_NOW)
        )

    def test_what_did_i_decide_last_week_bounds_something(self):
        """The plan's own worked example, bound half — same sentence shape,
        the other clause."""
        self.assertIsNotNone(
            recall._extract_temporal_bound("What did I decide last week?", now=_NOW)
        )

    def test_how_long_since_my_last_blog_post_abstains(self):
        """Gold question ep03's exact shape. 'my last blog post' is an event
        reference, not a calendar anchor — it is literally the unknown the
        question is asking to resolve."""
        self.assertIsNone(
            recall._extract_temporal_bound(
                "How long has it been since my last bog post?", now=_NOW
            )
        )

    def test_the_last_time_we_worked_on_x_abstains(self):
        """Gold question ep06's exact shape."""
        self.assertIsNone(
            recall._extract_temporal_bound(
                "When was the last time we worked on dev-setup?", now=_NOW
            )
        )

    def test_a_content_reference_to_a_month_does_not_match(self):
        """No trigger word ('in'/'since') precedes 'June' here — it is a
        topic label, not a temporal constraint."""
        self.assertIsNone(
            recall._extract_temporal_bound(
                "can you tell me about the June release notes", now=_NOW
            )
        )

    def test_a_possessive_month_reference_still_abstains(self):
        self.assertIsNone(
            recall._extract_temporal_bound("what were my June goals", now=_NOW)
        )

    def test_last_n_as_a_count_qualifier_does_not_match(self):
        """Gold question ng08's exact shape: 'last' modifies a count, not a
        day/week/month unit, so it must not be confused with 'last week'."""
        self.assertIsNone(
            recall._extract_temporal_bound(
                "Give me information on the last 5 security vulnerabilities "
                "that we fixed on shrimpi.",
                now=_NOW,
            )
        )


class SinceOpenEndedPriorityTests(unittest.TestCase):
    """'since <phrase>' must never fall through to the closed bare-phrase
    match, even though the bare phrase is a literal substring of it."""

    def test_since_a_relative_phrase_stays_open_ended_not_closed(self):
        self.assertEqual(
            recall._extract_temporal_bound("since last week", now=_NOW),
            ("2026-08-03", ""),
        )

    def test_since_yesterday_stays_open_ended(self):
        self.assertEqual(
            recall._extract_temporal_bound("since yesterday", now=_NOW),
            ("2026-08-13", ""),
        )

    def test_since_an_explicit_iso_date(self):
        self.assertEqual(
            recall._extract_temporal_bound("notes since 2020-01-15", now=_NOW),
            ("2020-01-15", ""),
        )

    def test_since_a_bare_year(self):
        self.assertEqual(
            recall._extract_temporal_bound("since 2020", now=_NOW),
            ("2020-01-01", ""),
        )


class FutureGuardTests(unittest.TestCase):
    """A resolved `after` on or after `now` can only ever match zero notes
    (nothing is captured in the future) — refused outright rather than
    shipped as a bound that silently guarantees emptiness."""

    def test_a_future_dated_month_is_refused(self):
        self.assertIsNone(
            recall._extract_temporal_bound("in December 2099", now=_NOW)
        )

    def test_a_future_bare_year_is_refused(self):
        self.assertIsNone(recall._extract_temporal_bound("since 2030", now=_NOW))

    def test_today_does_not_trip_the_future_guard(self):
        """`after == today` is the boundary case: today is not in the future,
        so this must still resolve rather than abstain."""
        self.assertEqual(
            recall._extract_temporal_bound("what changed today", now=_NOW),
            ("2026-08-14", "2026-08-15"),
        )

    def test_this_year_does_not_trip_the_future_guard(self):
        self.assertEqual(
            recall._extract_temporal_bound("this year's roadmap", now=_NOW),
            ("2026-01-01", "2027-01-01"),
        )


class SiblingPhraseTests(unittest.TestCase):
    """The closest unambiguous siblings of the five named examples."""

    def test_past_n_days_is_open_ended(self):
        self.assertEqual(
            recall._extract_temporal_bound("past 3 days of logs", now=_NOW),
            ("2026-08-11", ""),
        )

    def test_past_n_weeks_is_open_ended(self):
        self.assertEqual(
            recall._extract_temporal_bound("past 2 weeks of activity", now=_NOW),
            ("2026-07-31", ""),
        )

    def test_past_n_months_is_open_ended(self):
        self.assertEqual(
            recall._extract_temporal_bound("past 6 months of research", now=_NOW),
            ("2026-02-01", ""),
        )

    def test_last_month_bounds_the_previous_calendar_month(self):
        self.assertEqual(
            recall._extract_temporal_bound("last month's numbers", now=_NOW),
            ("2026-07-01", "2026-08-01"),
        )

    def test_last_year_bounds_the_previous_calendar_year(self):
        self.assertEqual(
            recall._extract_temporal_bound("last year's plan", now=_NOW),
            ("2025-01-01", "2026-01-01"),
        )

    def test_month_abbreviations_are_recognized(self):
        self.assertEqual(
            recall._extract_temporal_bound("in Jan", now=_NOW),
            ("2026-01-01", "2026-02-01"),
        )

    def test_a_month_not_yet_reached_this_year_resolves_to_last_years_occurrence(self):
        """Asked in March (before June has happened this year), 'in June'
        can only sensibly mean last June — this year's has not occurred."""
        march = datetime(2026, 3, 1, tzinfo=timezone.utc)
        self.assertEqual(
            recall._extract_temporal_bound("what did we ship in June", now=march),
            ("2025-06-01", "2025-07-01"),
        )

    def test_an_explicit_year_on_a_month_is_never_reinterpreted(self):
        self.assertEqual(
            recall._extract_temporal_bound("what happened in June 2024", now=_NOW),
            ("2024-06-01", "2024-07-01"),
        )


class ReferenceInstantInjectionTests(unittest.TestCase):
    """Deterministic means the same input at the same `now` always resolves
    the same way, and a different `now` resolves differently — the seam the
    plan's own text asks for so tests are never time-dependent."""

    def test_the_same_phrase_resolves_differently_under_a_different_now(self):
        first = recall._extract_temporal_bound(
            "yesterday", now=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        second = recall._extract_temporal_bound(
            "yesterday", now=datetime(2027, 6, 15, tzinfo=timezone.utc)
        )
        self.assertEqual(first, ("2026-01-01", "2026-01-02"))
        self.assertEqual(second, ("2027-06-14", "2027-06-15"))
        self.assertNotEqual(first, second)

    def test_omitting_now_uses_the_real_clock_and_does_not_raise(self):
        # No injected `now` — exercises the real datetime.now(timezone.utc)
        # branch. Anything on the calendar works; asserting only that this
        # does not raise and abstains cleanly on a contentless phrase keeps
        # the test itself time-independent.
        self.assertIsNone(recall._extract_temporal_bound("hello there"))


class GoldSetRegressionTests(unittest.TestCase):
    """Pins the measured finding the task's close-out reports: not one of
    the 84 goldv2 gold-set questions — including all 12 episodic-temporal
    ones — contains a phrase this extractor resolves with confidence. Every
    episodic-temporal question asks FOR a date ("when did...", "how long
    since...", "the last time..."); none supplies one to bound with. This
    is what makes the non-regression rule provably, not just measuredly,
    satisfied: the extractor never fires, so the hook's query path is
    byte-identical to `hook e2e` on this corpus. A future change to either
    the gold set or the extractor that makes this test fail is exactly the
    signal to re-run the full `--via-hook` comparison before trusting the
    non-regression claim again.
    """

    def test_no_gold_question_produces_a_bound(self):
        gold_path = (
            _HERE / "health" / "fixtures" / "week1-gold" / "gold-set-v2.json"
        )
        doc = json.loads(gold_path.read_text(encoding="utf-8"))
        fired = [
            (e["id"], recall._extract_temporal_bound(e["question"], now=_NOW))
            for e in doc["entries"]
        ]
        matched = [(i, b) for i, b in fired if b is not None]
        self.assertEqual(
            matched, [],
            f"expected no gold question to produce a bound, got: {matched}",
        )
        self.assertEqual(len(fired), 84, "the gold set's own question count moved")


class _FakeDaemon:
    """Stands in for `subprocess.run`, recording the argv it was handed —
    mirrors `test_recall_daemon_fast_path.py`'s own fixture of the same name.
    """

    def __init__(self, *, stdout: str = "", returncode: int = 0):
        self.stdout, self.returncode = stdout, returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


def _payload(*paths: str) -> str:
    return json.dumps({"results": [{"path": p, "score": 1.0} for p in paths], "matched": len(paths)})


class ArgvWiringTests(unittest.TestCase):
    """`_daemon_search` must add `-after`/`-before` exactly when the
    extractor resolves a bound, and never otherwise — mirrors
    `test_recall_daemon_fast_path.py`'s `ModeAndQuestionWiringTests` style.
    Both prompts here are chosen to resolve the same way regardless of the
    real wall-clock day the suite runs on, since `_daemon_search` calls the
    extractor with the real `now` (no injection seam at this layer) — an
    explicit ISO date for the positive case, a phrase with no date content
    at all for the negative one.
    """

    def _sent(self, prompt: str) -> list[str]:
        fake = _FakeDaemon(stdout=_payload())
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(recall.subprocess, "run", fake):
                recall._daemon_search(vault=Path(tmp), query_text=prompt, status={})
        return fake.calls[0]

    def test_a_resolved_bound_adds_after_and_before_to_argv(self):
        argv = self._sent("what changed in June 2024 with the vault transport")
        self.assertIn("-after", argv)
        self.assertEqual(argv[argv.index("-after") + 1], "2024-06-01")
        self.assertIn("-before", argv)
        self.assertEqual(argv[argv.index("-before") + 1], "2024-07-01")

    def test_an_open_ended_bound_omits_before_but_adds_after(self):
        argv = self._sent("vault transport notes since 2020-01-15")
        self.assertIn("-after", argv)
        self.assertEqual(argv[argv.index("-after") + 1], "2020-01-15")
        self.assertNotIn("-before", argv)

    def test_no_match_adds_neither_flag(self):
        argv = self._sent("what did we decide about the vault git transport?")
        self.assertNotIn("-after", argv)
        self.assertNotIn("-before", argv)

    def test_the_positional_terms_are_unaffected_by_a_resolved_bound(self):
        """The bound is additive — it must not change what the lexical arms
        search for, only add a filter on top."""
        argv = self._sent("what changed in June 2024 with the vault transport")
        self.assertEqual(argv[-1], "changed june 2024 vault transport")


class TransparencyLineTests(unittest.TestCase):
    """The stderr transparency line names the bound when one was applied,
    and stays silent about it otherwise — mirrors
    `test_recall_daemon_fast_path.py`'s `HookCutoverInjectionTests`.
    """

    def _submit(self, fake, prompt: str):
        import io

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Agent"
            vault.mkdir(parents=True)
            out, err = io.StringIO(), io.StringIO()
            with unittest.mock.patch.object(recall.subprocess, "run", fake):
                recall.prompt_submit(vault=vault, prompt=prompt, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_a_resolved_bound_is_named_on_the_transparency_line(self):
        _, err = self._submit(
            _FakeDaemon(stdout=_payload()), "vault transport notes since 2020-01-15"
        )
        self.assertIn("temporal: after=2020-01-15 before=—", err)

    def test_no_match_leaves_the_transparency_line_unchanged(self):
        _, err = self._submit(
            _FakeDaemon(stdout=_payload()),
            "what did we decide about the vault git transport?",
        )
        self.assertNotIn("temporal:", err)


if __name__ == "__main__":
    unittest.main()
