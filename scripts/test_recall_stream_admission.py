#!/usr/bin/env python3
"""Tests for recall.py's stream-admission contract (GH #92).

The bug these pin: on any machine with `sentence-transformers` installed --
`embed.py`'s documented DEFAULT mode -- `memory-recall-prompt-submit` returned
ZERO entries for every prompt, taking ~4s to do it. The hook still fired, still
exited 0, and still reported the overrun honestly, so nothing anywhere went
red. It was indistinguishable from an empty vault.

The mechanism was an ordering-and-accounting problem in three parts:

  1. `_vec_search` embedded the query BEFORE checking whether the vector index
     could be opened at all. A cold sentence-transformers load costs ~3900ms
     against a 300ms budget, and on a host whose sqlite3 lacks
     `enable_load_extension` (macOS system Python) the index can NEVER open --
     so the entire budget was spent producing a vector with nowhere to go.
  2. Having blown the budget, `_vec_search` then re-checked the deadline and
     discarded the embedding it had just paid for.
  3. `_bm25_search` ran next, found no time left, and returned {} on its first
     loop iteration -- despite the call site's comment promising "even if vec
     consumed most of the budget, we try it."

Both halves empty -> zero results.

The contract adopted in response, and pinned here: **a recall stream runs to
completion or does not run at all.** Cheap checks precede expensive ones, an
unaffordable cold model load is declined rather than attempted-and-discarded,
and a corpus walk cut short by the deadline has its results thrown away rather
than fused. That last point is not fastidiousness: `_bm25_search` computes IDF
and `avgdl` over the candidate set the walk actually reached, so a truncated
walk yields a *differently-scored ranking over an arbitrary subset*, not a
prefix of the real one. On the operator's 1745-entry vault a 300ms budget
reaches ~70 entries and confidently reports whichever ones the directory walk
happened to visit first.

These tests are hermetic: no `sentence-transformers`, no `sqlite-vec`, no
network. The expensive dependency is replaced by a spy that records whether it
was called, which is the actual assertion for the ordering rules -- pinning
"the embedder was never invoked" is what stops the 4s cost from silently
returning, and unlike a wall-clock assertion it holds on any machine.

**Timing discipline.** Every test here is about which admission branch a given
budget selects. None is about how fast the host is, and any test that lets the
two get mixed up is testing the runner. Two ways that has actually happened:

  - `deadline=time.monotonic() + 0.300` reads as "300ms of budget" but delivers
    "300ms minus however long this host took to get here", which on a loaded
    shared runner has been the entire budget. Use `_a_budget_of` instead, which
    freezes the clock so the figure is exact.
  - A test that asserts something about a search which RAN has a precondition
    the host can fail to meet. Give it `_GENEROUS_BUDGET_MS` and call
    `_require_a_completed_search` first, so a starved run says it was starved
    rather than failing on the downstream assertion as if the code were broken.

A test that needs the opposite -- a stream that was blocked -- cannot get there
by asking for a 1ms budget either, however safe that sounds. That is the hook
suites' idiom and it relies on a fresh interpreter and a real index open to
burn the millisecond; in-process, with every module already imported, a
one-entry vault walks well inside it. Use `_an_exhausted_budget`, which elapses
the deadline on a fake clock rather than betting on the host being slow.

Run: python3 scripts/test_recall_stream_admission.py
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import recall  # noqa: E402

# 10x the production interactive budget, for the tests whose subject is what a
# completed search reports rather than how long one takes. Same figure and same
# reasoning as the hook suites' fixtures (PR #418).
_GENEROUS_BUDGET_MS = 3000


def _write_entry(directory: Path, slug: str, body: str, *, tags: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(
        f"---\nname: {slug}\nkind: fix\ntags: [{tags}]\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class _EmbedSpy:
    """Stands in for `embed.embed_text`, recording whether it ran.

    Deliberately returns a valid-shaped vector rather than raising: a spy that
    blew up would pass these tests for the wrong reason (the caller bailing on
    an exception rather than declining to call it in the first place).
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, text, *, mode=None):
        self.calls += 1
        return [0.0] * embed.EMBEDDING_DIM


class _FakeConn:
    """Minimal stand-in for an open sqlite-vec connection with an empty index.

    Returning no rows is the point: these tests assert on whether the embedder
    ran, not on ranking, and an empty result set keeps them from accidentally
    depending on vec0 semantics that aren't available in this environment.
    """

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class _FakeClock:
    """Monotonic clock that advances a fixed step on every read.

    Wall-clock timing cannot express "stop the walk at entry 20" reproducibly —
    a real budget small enough to truncate on a fast machine truncates at zero
    on a slow one, and the difference between those two is exactly the
    difference between this suite testing the discard and not testing it.
    Counting reads makes the truncation point a property of the test rather
    than of the machine running it.
    """

    def __init__(self, start: float = 1000.0, step: float = 0.001):
        self._t = start
        self._step = step

    def now(self) -> float:
        """Read the current value WITHOUT advancing — for computing deadlines."""
        return self._t

    def __call__(self) -> float:
        value = self._t
        self._t += self._step
        return value


@contextlib.contextmanager
def _a_budget_of(ms: float):
    """Hold exactly `ms` of remaining budget open for the duration of the call.

    `deadline = time.monotonic() + 0.300` states an intent the wall clock does
    not keep. It reads as "300ms of budget", but what the code under test
    actually sees is "300ms minus however long this host took to get from that
    line to the admission check" -- and on a loaded shared runner that
    difference has been the whole budget. A frozen clock makes the number the
    test names the number the code reads, on any machine.

    Freezing rather than expiring is the point: these callers are about which
    branch a given remaining budget selects, so the budget has to still be
    there when the branch is chosen.
    """
    clock = _FakeClock(step=0.0)
    deadline = clock.now() + ms / 1000.0
    with unittest.mock.patch.object(recall.time, "monotonic", clock):
        yield deadline


@contextlib.contextmanager
def _an_exhausted_budget():
    """Elapse the budget before the first stream is reached, on any host.

    The obvious alternative -- ask for a 1ms budget and rely on setup costing
    more than that -- is the hook suites' idiom, and it does not transfer
    in-process. There, a fresh interpreter and a real sqlite-vec index open
    guarantee the starvation. Here every module is already imported, and a host
    whose sqlite3 cannot load extensions declines the index in microseconds, so
    a one-entry vault finishes comfortably inside 1ms; observed doing exactly
    that on macOS system Python, which reported `Loaded 1` where the test wanted
    a blocked stream. A clock that jumps a full second per read starves the walk
    everywhere instead of on the slow half of the fleet.

    Safe to patch globally for the duration: `vault_lock` reads the same clock,
    but only inside its contention-retry loop, which an uncontended write in a
    fresh temp vault never enters.
    """
    with unittest.mock.patch.object(recall.time, "monotonic", _FakeClock(step=1.0)):
        yield


class LexicalCompletenessTests(unittest.TestCase):
    """Rule 2: a truncated corpus walk is discarded, never fused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        # Enough entries that a mid-walk deadline leaves a real remainder.
        for i in range(120):
            _write_entry(
                self.vault / "memory", f"entry-{i:03d}",
                f"Entry {i} discusses quokka migration and zorbulax resets.",
            )
        self.addCleanup(self._tmp.cleanup)

    def test_complete_walk_reports_complete(self):
        status: dict = {}
        results = recall._bm25_search(self.vault, ["quokka"], status=status)
        self.assertTrue(status["complete"])
        self.assertEqual(status["walked"], status["total"])
        self.assertTrue(results, "sanity: the corpus does contain 'quokka'")

    def test_elapsed_deadline_marks_the_walk_incomplete(self):
        status: dict = {}
        recall._bm25_search(
            self.vault, ["quokka"],
            deadline=time.monotonic() - 1.0, status=status,
        )
        self.assertFalse(status["complete"])
        self.assertEqual(status["walked"], 0)
        self.assertEqual(status["total"], 120)

    def test_query_discards_an_incomplete_lexical_ranking(self):
        """The behavioral heart of the fix.

        Before GH #92's repair the truncated ranking was fused and returned,
        so recall confidently surfaced whichever entries the directory walk
        reached first. Returning nothing is the correct answer here — an
        arbitrary ranking presented as a real one is worse than silence,
        because nothing downstream can tell the difference.

        The deadline has to elapse PARTWAY through the walk, which is why this
        drives a fake clock instead of passing an already-expired deadline. An
        expired deadline stops the walk at zero entries, so the results are
        empty before any discarding happens and the test passes whether or not
        the discard exists — verified by mutation: deleting the discard left an
        earlier version of this test green. The two assertions that matter are
        paired: the lexical half alone must produce a NON-EMPTY partial
        ranking, and `query()` given the same deadline must return nothing.
        """
        clock = _FakeClock(step=0.001)
        deadline = clock.now() + 0.050  # trips ~50 calls in, mid-walk

        with unittest.mock.patch.object(recall.time, "monotonic", clock):
            lexical_status: dict = {}
            partial = recall._bm25_search(
                self.vault, ["quokka"], deadline=deadline, status=lexical_status,
            )

        self.assertFalse(lexical_status["complete"])
        self.assertGreater(
            lexical_status["walked"], 0,
            "the fake clock must let the walk start, or this test cannot "
            "distinguish discarding from never having anything to discard",
        )
        self.assertLess(lexical_status["walked"], lexical_status["total"])
        self.assertTrue(
            partial,
            "the truncated walk must produce a non-empty ranking — that "
            "arbitrary ranking is precisely what must not reach the caller",
        )

        clock2 = _FakeClock(step=0.001)
        deadline2 = clock2.now() + 0.050
        with unittest.mock.patch.object(recall.time, "monotonic", clock2):
            status: dict = {}
            results = recall.query(
                vault=self.vault, query_text="quokka",
                deadline=deadline2, status=status,
            )

        self.assertFalse(status["lexical"]["complete"])
        self.assertEqual(
            results, [],
            "a partially-walked ranking reached the caller; it must be "
            "discarded, not fused",
        )

    def test_expired_deadline_yields_nothing_to_discard(self):
        """The trivial case, kept separate so the one above stays honest."""
        status: dict = {}
        results = recall.query(
            vault=self.vault,
            query_text="quokka migration",
            deadline=time.monotonic() - 1.0,
            status=status,
        )
        self.assertEqual(results, [])
        self.assertFalse(status["lexical"]["complete"])
        self.assertFalse(status["searched"])

    def test_status_records_coverage_for_a_partial_walk(self):
        """`walked` / `total` must be real numbers, not placeholders.

        They are what the transparency line quotes back to the operator, and a
        hardcoded 0-of-0 would read as plausible while telling them nothing.

        The fake clock is doing the same job here as in the discard test above.
        This used to pass a real 0.5ms deadline and then assert only
        `walked <= total`, which holds for a walk that completed, a walk that
        was truncated, and a walk that never started -- so it could not tell a
        real coverage report from a placeholder, which is the one thing it
        exists to check. The partial walk is now established rather than hoped
        for, and both numbers are pinned against it.
        """
        clock = _FakeClock(step=0.001)
        deadline = clock.now() + 0.050  # trips ~50 calls in, mid-walk
        status: dict = {}
        with unittest.mock.patch.object(recall.time, "monotonic", clock):
            recall._bm25_search(
                self.vault, ["quokka"], deadline=deadline, status=status,
            )
        self.assertFalse(status["complete"])
        self.assertEqual(status["total"], 120)
        self.assertGreater(status["walked"], 0)
        self.assertLess(status["walked"], status["total"])


class TransparencyTests(unittest.TestCase):
    """Rule 3: zero results must say whether a search actually happened."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        _write_entry(
            self.vault / "memory", "zorbulax",
            "The zorbulax subsystem requires a manual reset after a cold boot.",
        )
        self.addCleanup(self._tmp.cleanup)

    def _run(self, prompt: str, budget_ms: int):
        import io
        out, err = io.StringIO(), io.StringIO()
        recall.prompt_submit(
            vault=self.vault, prompt=prompt, budget_ms=budget_ms,
            stdout=out, stderr=err,
        )
        return out.getvalue(), err.getvalue()

    def _require_a_completed_search(self, stderr: str) -> None:
        """Precondition for the tests below whose subject is a search that RAN.

        Those tests say something about what a completed search reports. A host
        too slow to complete one has not met their precondition, and whatever
        they would assert next is then a statement about the runner.
        `_GENEROUS_BUDGET_MS` is what makes that vanishingly unlikely; this is
        what stops it being silent on the day it happens anyway. It fails with
        the starvation named, rather than leaving the downstream assertion to
        fail with a message that reads like a real defect -- which is how the
        Windows install-smoke failures at f5f2307 were first read.

        Keyed on the discarded walk rather than on the overrun warning, which
        is the broader signal and would over-trigger: a run that crossed its
        budget on the final check but walked the whole corpus has met the
        precondition, and failing it would trade one flake for another.
        """
        if "lexical: discarded" in stderr:
            self.fail(
                "budget-starved run, not a defect in the code under test: "
                f"{_GENEROUS_BUDGET_MS}ms was not enough for this host to walk "
                "a one-entry vault, so the lexical half was discarded and no "
                "search completed -- there is nothing here to assert about. "
                "Re-run on an idle machine, or widen _GENEROUS_BUDGET_MS "
                f"(staying under {recall.VEC_COLD_EMBED_MIN_BUDGET_MS}ms, or the "
                "vec half stops being declined on affordability and these become "
                f"different tests). stderr:\n{stderr}"
            )

    def test_a_matching_entry_is_injected_when_the_budget_is_sufficient(self):
        """The end-to-end repro from GH #92, inverted into a passing case.

        Reported symptom: this exact shape returned zero entries and took ~4s.

        This used to run at `PROMPT_SUBMIT_BUDGET_MS` and was renamed off it,
        because "within the default budget" is a claim about how fast the host
        is, not about what recall does, and a shared CI runner cannot honor it.
        It failed exactly that way on Linux (PR #418): stdout empty, same
        signature as the Windows hook flake, with no hook or subprocess in the
        picture — a recall pays a fixed cost before the corpus walk starts, and
        under load that alone exhausts 300ms, so the walk is discarded before
        entry 0. Asserting injection under a budget the machine can actually
        meet keeps the behavior and drops the performance claim.

        """
        stdout, stderr = self._run("how do I reset zorbulax", _GENEROUS_BUDGET_MS)
        self._require_a_completed_search(stderr)
        self.assertIn("zorbulax", stdout)
        self.assertIn("Loaded 1 relevant entries", stderr)
        # There is one stream now that the vector half is gone, so a run that
        # loaded an entry must not report any stream as blocked.
        self.assertNotIn("NOTHING WAS SEARCHED", stderr)

    def test_an_overrun_is_reported_even_when_no_stream_was_attempted(self):
        """The forced-overrun path still has to announce its overrun.

        `budget_ms <= 0` short-circuits before `query()` is called, so no
        stream reports back and there are no blocked streams to name -- the
        overrun warning is the only signal the operator gets, and it has to be
        there. The blocked-stream wording is a different path, pinned by
        `test_a_blocked_search_is_labelled_unsearched` below; this test was
        named for it and never reached it.
        """
        _, stderr = self._run("how do I reset zorbulax", budget_ms=-1)
        self.assertIn("Loaded 0 relevant entries", stderr)
        self.assertIn("WARNING", stderr)

    def test_a_blocked_search_is_labelled_unsearched(self):
        """The observability half of the bug, pinned positively.

        `Loaded 0` read identically whether the vault was genuinely empty of
        matches or no stream had run at all, which is why GH #92 survived
        months of green CI. A zero caused by a blocked stream must say so, and
        must name which streams and why -- the reason is what tells the
        operator whether to widen a budget or repair an index.

        This is the assertion the converse below is the converse OF. Without it
        `NOTHING WAS SEARCHED` is only ever asserted absent, and deleting the
        wording from recall.py outright would keep this suite green: the same
        shape of gap the wording exists to close.

        Runs at the production budget and exhausts it on a fake clock, so this
        is the operator's real interactive scenario rather than an invented
        one, reproduced without asking anything of the host. The budget still
        has to be positive: a non-positive one takes the forced-overrun branch
        above, which never calls `query()` and so has no blocked streams to
        report -- which is why a matching entry that IS found here would be the
        interesting failure, not a stray zero.
        """
        with _an_exhausted_budget():
            _, stderr = self._run(
                "how do I reset zorbulax", recall.PROMPT_SUBMIT_BUDGET_MS,
            )
        self.assertIn("Loaded 0 relevant entries", stderr)
        self.assertIn("NOTHING WAS SEARCHED", stderr)
        self.assertIn("this is not an empty vault", stderr)
        self.assertIn("lexical:", stderr)
        self.assertIn("WARNING", stderr)

    def test_genuine_no_match_does_not_claim_nothing_was_searched(self):
        """The converse, so the wording above can't just always fire.

        A search that really ran and really found nothing must NOT be labelled
        as un-searched, or the distinction is decorative.

        Runs at the fixture budget rather than `PROMPT_SUBMIT_BUDGET_MS`, which
        is what it used to do. At 300ms it asserted on a state it had not
        established: on the Windows install-smoke runner the fixed pre-walk
        setup alone exhausted the budget, the walk was discarded before entry 0
        of 1, and `NOTHING WAS SEARCHED` was then the CORRECT output for a
        search that genuinely had not run. Twice on 2026-08-09, on two
        unrelated PRs, green on re-run both times. The wording it guards is
        real behavior, so the precondition moves rather than the assertion --
        and the starved case it was accidentally exercising is now pinned on
        purpose, above.
        """
        _, stderr = self._run("entirely unrelated aardvark topic", _GENEROUS_BUDGET_MS)
        self._require_a_completed_search(stderr)
        self.assertIn("Loaded 0 relevant entries", stderr)
        self.assertNotIn("NOTHING WAS SEARCHED", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
