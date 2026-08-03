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

Run: python3 scripts/test_recall_stream_admission.py
"""
from __future__ import annotations

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

import embed  # noqa: E402
import recall  # noqa: E402
import vec_index  # noqa: E402


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


class VecSearchAdmissionTests(unittest.TestCase):
    """Rule 1: cheap checks run before expensive ones."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        _write_entry(self.vault / "personal", "zorbulax", "The zorbulax subsystem needs a reset.")
        self.spy = _EmbedSpy()
        self._real_embed = embed.embed_text
        self._real_resident = embed.local_model_is_resident
        self._real_open = vec_index._open_index
        embed.embed_text = self.spy
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore)

    def _restore(self):
        embed.embed_text = self._real_embed
        embed.local_model_is_resident = self._real_resident
        vec_index._open_index = self._real_open

    def test_unopenable_index_skips_the_embedding_entirely(self):
        """The headline regression: no index means no embedding cost.

        This is the exact shape of GH #92 on macOS system Python, whose sqlite3
        has no `enable_load_extension`, so `_open_index` returns None on every
        call forever. Embedding first meant paying ~3900ms per prompt to build
        a vector that was then thrown away unused.
        """
        vec_index._open_index = lambda vault: None
        status: dict = {}
        results = recall._vec_search(
            self.vault, "zorbulax", k=5,
            deadline=time.monotonic() + 0.300, status=status,
        )
        self.assertEqual(results, {})
        self.assertEqual(
            self.spy.calls, 0,
            "embed_text was called despite the index being unopenable — the "
            "cheap check must precede the expensive one, or the 4s-per-prompt "
            "cost is back",
        )
        self.assertFalse(status["ran"])
        self.assertIn("sqlite-vec", status["reason"])

    def test_cold_model_is_declined_under_an_interactive_budget(self):
        """A cold load cannot fit 300ms, so it is not started."""
        vec_index._open_index = lambda vault: _FakeConn()
        embed.local_model_is_resident = lambda mode=None: False
        status: dict = {}
        results = recall._vec_search(
            self.vault, "zorbulax", k=5,
            deadline=time.monotonic() + 0.300, status=status,
        )
        self.assertEqual(results, {})
        self.assertEqual(
            self.spy.calls, 0,
            "a cold model load was started inside a 300ms budget it cannot "
            "possibly fit",
        )
        self.assertFalse(status["ran"])
        self.assertIn("not loaded", status["reason"])

    def test_cold_model_is_attempted_when_the_budget_can_absorb_it(self):
        """The `query` CLI's 10s budget still gets real semantic search.

        The fix must not become "never embed" — an explicit operator-initiated
        search can afford the load, and that is where semantic ranking earns
        its cost. Without this test, hard-declining every cold embed would look
        like a pass.
        """
        vec_index._open_index = lambda vault: _FakeConn()
        embed.local_model_is_resident = lambda mode=None: False
        status: dict = {}
        recall._vec_search(
            self.vault, "zorbulax", k=5,
            deadline=time.monotonic() + (recall.QUERY_CLI_BUDGET_MS / 1000.0),
            status=status,
        )
        self.assertEqual(
            self.spy.calls, 1,
            "a 10s budget can absorb a ~3.9s cold load; declining it would "
            "strip semantic recall from the query CLI too",
        )

    def test_warm_model_is_used_even_on_a_tight_budget(self):
        """~15ms warm fits 300ms comfortably; only the cold path is refused."""
        vec_index._open_index = lambda vault: _FakeConn()
        embed.local_model_is_resident = lambda mode=None: True
        recall._vec_search(
            self.vault, "zorbulax", k=5,
            deadline=time.monotonic() + 0.300,
        )
        self.assertEqual(self.spy.calls, 1)

    def test_no_deadline_means_no_admission_gate(self):
        """A caller that passes no deadline has opted out of budgeting."""
        vec_index._open_index = lambda vault: _FakeConn()
        embed.local_model_is_resident = lambda mode=None: False
        recall._vec_search(self.vault, "zorbulax", k=5, deadline=None)
        self.assertEqual(self.spy.calls, 1)


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


class LexicalCompletenessTests(unittest.TestCase):
    """Rule 2: a truncated corpus walk is discarded, never fused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        # Enough entries that a mid-walk deadline leaves a real remainder.
        for i in range(120):
            _write_entry(
                self.vault / "personal", f"entry-{i:03d}",
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
        """
        status: dict = {}
        # A deadline that elapses partway through the walk rather than before it.
        recall._bm25_search(
            self.vault, ["quokka"],
            deadline=time.monotonic() + 0.0005, status=status,
        )
        self.assertEqual(status["total"], 120)
        self.assertLessEqual(status["walked"], status["total"])


class TransparencyTests(unittest.TestCase):
    """Rule 3: zero results must say whether a search actually happened."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        _write_entry(
            self.vault / "personal", "zorbulax",
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

    def test_a_matching_entry_is_injected_within_the_default_budget(self):
        """The end-to-end repro from GH #92, inverted into a passing case.

        Reported symptom: this exact shape returned zero entries and took ~4s.
        """
        stdout, stderr = self._run("how do I reset zorbulax", recall.PROMPT_SUBMIT_BUDGET_MS)
        self.assertIn("zorbulax", stdout)
        self.assertIn("Loaded 1 relevant entries", stderr)

    def test_unsearched_is_reported_differently_from_no_matches(self):
        """The observability half of the bug.

        `Loaded 0` read identically whether the vault was genuinely empty of
        matches or no stream had run at all, which is why this survived months
        of green CI. A zero caused by a blocked stream must say so.
        """
        _, stderr = self._run("how do I reset zorbulax", budget_ms=-1)
        self.assertIn("Loaded 0 relevant entries", stderr)
        # budget_ms <= 0 is the forced-overrun path: nothing is searched.
        self.assertIn("WARNING", stderr)

    def test_genuine_no_match_does_not_claim_nothing_was_searched(self):
        """The converse, so the new wording can't just always fire.

        A search that really ran and really found nothing must NOT be labelled
        as un-searched, or the distinction is decorative.
        """
        _, stderr = self._run("entirely unrelated aardvark topic", recall.PROMPT_SUBMIT_BUDGET_MS)
        self.assertIn("Loaded 0 relevant entries", stderr)
        self.assertNotIn("NOTHING WAS SEARCHED", stderr)


class ResidencyProbeTests(unittest.TestCase):
    """`local_model_is_resident` must never trigger the load it measures."""

    def test_probe_is_false_before_any_local_embed(self):
        # Whether sentence-transformers is importable is machine-dependent, so
        # assert the invariant that holds either way: the probe answers without
        # importing the package itself.
        if "sentence_transformers" in sys.modules:
            self.skipTest("sentence-transformers already imported in this process")
        self.assertFalse(embed.local_model_is_resident("local"))
        self.assertNotIn(
            "sentence_transformers", sys.modules,
            "the residency probe imported the very package it exists to avoid "
            "loading",
        )

    def test_stub_mode_is_always_resident(self):
        self.assertTrue(embed.local_model_is_resident("stub"))

    def test_unknown_mode_is_not_resident_and_does_not_raise(self):
        self.assertFalse(embed.local_model_is_resident("nonsense-mode"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
