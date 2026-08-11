#!/usr/bin/env python3
"""Tests for the week-1 retrieval experiment runner.

Expected values here are written by hand from what the behavior should be, not
computed with the implementation's own formula. A check that derives its
expectation the same way the code does proves only that the code agrees with
itself, which is how this repo previously shipped 57 days of silently-dead
session reflection under fully green CI.

The lexical tests build a real FTS5 index over a real (tiny, throwaway) vault on
disk, because the thing worth checking is that SQLite's built-in FTS5 ranks the
way the experiment needs it to under Apple's system Python. Vector tests use
`embed.py`'s stub mode: hash vectors carry no semantics, so they can only prove
plumbing — which is all they are asked to prove.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts", _REPO / "harness" / "skills" / "memory" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import eval_v6_retrieval as ev  # noqa: E402
import week1_corpus as wc  # noqa: E402

# The vector arm needs numpy. CI's unit-test job does not install it, so those
# tests skip there rather than erroring — an ImportError reported as a test
# failure says "the code is broken" when it means "the optional dependency is
# absent", and eleven of those buried a real signal on the first PR to run this
# suite. The lexical arm, which is what the daemon actually ships, needs nothing
# beyond the standard library and is never skipped.
try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy not installed — vector arm only")
import week1_retrieval_experiment as w1  # noqa: E402
import week1_search_daemon as wd  # noqa: E402
import week1_search_shim as ws  # noqa: E402


def _make_vault(tmp):
    """A five-note vault with hand-known contents, so rankings can be asserted."""
    v = Path(tmp) / "vault"
    (v / "memory" / "_always-load").mkdir(parents=True)
    (v / "desk/projects").mkdir(parents=True)
    notes = {
        "memory/_always-load/commit-no-coauthor-trailer.md":
            "---\nkind: convention\ntags: [git, commits]\n---\n"
            "Do not append a Co-Authored-By trailer to git commit messages.\n",
        "memory/_always-load/wake-on-ci-pattern.md":
            "---\nkind: convention\n---\n"
            "Never mark a task done speculatively. Wait for the check suite.\n",
        "desk/projects/unrelated-gardening.md":
            "---\nkind: note\n---\nTomatoes want full sun and deep watering.\n",
        "desk/projects/long-note.md":
            "---\nkind: note\n---\n" + ("filler paragraph.\n\n" * 200)
            + "\nThe mooring permit was refused by the harbour authority.\n",
        ".hidden/should-not-be-indexed.md": "secret\n",
    }
    for rel, body in notes.items():
        p = v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return v


class TestScoreAtK(unittest.TestCase):
    """The metric itself. Literals, not re-derivations."""

    def test_precision_divides_by_k_not_by_result_count(self):
        # One expected note, found, among two returned. Precision@5 counts
        # against a denominator of 5 — returning two right answers is not
        # perfect precision at 5.
        s = ev.score_at_k(["a.md"], ["a.md", "b.md"], k=5)
        self.assertEqual(s["p_at_k"], 0.2)
        self.assertEqual(s["r_at_k"], 1.0)
        self.assertEqual(s["first_hit_rank"], 1)

    def test_partial_recall_across_two_expected_notes(self):
        s = ev.score_at_k(["a.md", "b.md"], ["x.md", "b.md"], k=5)
        self.assertEqual(s["p_at_k"], 0.2)
        self.assertEqual(s["r_at_k"], 0.5)
        self.assertEqual(s["first_hit_rank"], 2)

    def test_complete_miss_scores_zero_and_has_no_rank(self):
        s = ev.score_at_k(["a.md"], ["x.md", "y.md"], k=5)
        self.assertEqual((s["p_at_k"], s["r_at_k"]), (0.0, 0.0))
        self.assertIsNone(s["first_hit_rank"])

    def test_results_past_k_do_not_count(self):
        ranked = ["x.md", "y.md", "z.md", "w.md", "v.md", "a.md"]
        self.assertEqual(ev.score_at_k(["a.md"], ranked, k=5)["r_at_k"], 0.0)

    def test_negative_stratum_rewards_returning_nothing(self):
        correct = ev.score_at_k([], [], k=5)
        self.assertEqual((correct["p_at_k"], correct["r_at_k"]), (1.0, 1.0))
        self.assertTrue(correct["correct_rejection"])
        self.assertTrue(correct["is_negative"])

    def test_negative_stratum_punishes_a_confident_guess(self):
        wrong = ev.score_at_k([], ["something.md"], k=5)
        self.assertEqual((wrong["p_at_k"], wrong["r_at_k"]), (0.0, 0.0))
        self.assertFalse(wrong["correct_rejection"])


class TestEvalV6RegressionAfterExtraction(unittest.TestCase):
    """`run_eval` must produce exactly what it produced before score_at_k existed."""

    def test_run_eval_numbers_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            qs = Path(tmp) / "q.json"
            qs.write_text(json.dumps({"entries": [
                {"id": "q1", "query": "alpha", "expected_notes": ["a.md"]},
                {"id": "q2", "query": "beta", "expected_notes": ["b.md", "c.md"]},
            ]}), encoding="utf-8")
            vault = Path(tmp) / "v"
            for name in ("a.md", "b.md", "c.md"):
                (vault).mkdir(exist_ok=True)
                (vault / name).write_text("x", encoding="utf-8")

            # old finds a.md only; new finds a.md and b.md.
            res = ev.run_eval(
                vault, qs,
                old_top_k_fn=lambda v, q, k=5: ["a.md"] if q == "alpha" else [],
                new_top_k_fn=lambda v, q, k=5: ["a.md"] if q == "alpha" else ["b.md"],
            )
            acc = res["accuracy"]
            # q1: hits 1/1 -> P 1/5=0.2, R 1.0.  q2 old: 0.  Averaged over 2.
            self.assertAlmostEqual(acc["old_p_at_5"], 0.1)
            self.assertAlmostEqual(acc["old_r_at_5"], 0.5)
            # q2 new: 1 of 2 expected -> P 0.2, R 0.5. Averages: P 0.2, R 0.75.
            self.assertAlmostEqual(acc["new_p_at_5"], 0.2)
            self.assertAlmostEqual(acc["new_r_at_5"], 0.75)
            self.assertEqual(res["discovery_rate"]["new_found_old_missed_pairs"], 1)
            self.assertEqual(res["compression"]["old_avg_rank_to_first_hit"], 1.0)


class TestGoldSetValidation(unittest.TestCase):
    def _write(self, tmp, entries):
        p = Path(tmp) / "gold.json"
        p.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        return p

    def test_valid_set_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, [{"id": "a", "question": "q?",
                                   "expected_note_paths": ["n.md"],
                                   "stratum": "distinctive-token",
                                   "source": "transcript"}])
            self.assertEqual(len(w1.load_gold_set(p)), 1)

    def test_bare_list_is_also_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "g.json"
            p.write_text(json.dumps([{"id": "a", "question": "q?",
                                      "expected_note_paths": [], "stratum": "negative",
                                      "source": "cold"}]), encoding="utf-8")
            self.assertEqual(len(w1.load_gold_set(p)), 1)

    def test_bad_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, [{"id": "a", "question": "q?", "expected_note_paths": [],
                                   "stratum": "negative", "source": "made-up"}])
            with self.assertRaises(SystemExit) as cm:
                w1.load_gold_set(p)
            self.assertIn("source", str(cm.exception))

    def test_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = {"id": "dup", "question": "q?", "expected_note_paths": [],
                 "stratum": "negative", "source": "cold"}
            p = self._write(tmp, [e, dict(e)])
            with self.assertRaises(SystemExit) as cm:
                w1.load_gold_set(p)
            self.assertIn("duplicate id", str(cm.exception))

    def test_every_problem_is_reported_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, [{"id": "", "question": "", "expected_note_paths": "no",
                                   "stratum": "", "source": "nope"}])
            with self.assertRaises(SystemExit) as cm:
                w1.load_gold_set(p)
            msg = str(cm.exception)
            for expected in ("'id'", "'question'", "'stratum'",
                             "expected_note_paths", "source"):
                self.assertIn(expected, msg)

    def test_missing_expected_paths_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "v"
            vault.mkdir()
            (vault / "here.md").write_text("x", encoding="utf-8")
            entries = [
                {"id": "a", "question": "q", "expected_note_paths": ["here.md"],
                 "stratum": "s", "source": "cold"},
                {"id": "b", "question": "q", "expected_note_paths": ["gone.md"],
                 "stratum": "s", "source": "cold"},
                {"id": "c", "question": "q", "expected_note_paths": [],
                 "stratum": "negative", "source": "cold"},
            ]
            self.assertEqual(w1.check_expected_paths_exist(entries, vault), ["gone.md"])


class TestParseAnswer(unittest.TestCase):
    def test_plain_answer(self):
        self.assertEqual(w1.parse_answer("ANSWER: a/b.md, c/d.md"),
                         ("answer", ["a/b.md", "c/d.md"]))

    def test_no_answer_found(self):
        self.assertEqual(w1.parse_answer("blah\nANSWER: no answer found"),
                         ("no_answer", []))

    def test_last_answer_line_wins(self):
        text = "ANSWER: wrong.md\nOn reflection:\nANSWER: right.md"
        self.assertEqual(w1.parse_answer(text), ("answer", ["right.md"]))

    def test_decoration_is_stripped(self):
        got = w1.parse_answer('ANSWER: `a/b.md`, "/c/d.md"')
        self.assertEqual(got, ("answer", ["a/b.md", "c/d.md"]))

    def test_at_most_five_paths_are_scored(self):
        listed = ", ".join(f"n{i}.md" for i in range(9))
        kind, paths = w1.parse_answer(f"ANSWER: {listed}")
        self.assertEqual(len(paths), 5)
        self.assertEqual(paths[0], "n0.md")

    def test_missing_answer_line_is_distinguishable_from_no_answer(self):
        self.assertEqual(w1.parse_answer("I looked but never concluded."), (None, []))


class TestCorpusWalk(unittest.TestCase):
    def test_walk_finds_notes_and_skips_dot_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            rels = sorted(p.relative_to(v).as_posix() for p in wc.iter_markdown_paths(v))
            self.assertEqual(rels, [
                "desk/projects/long-note.md",
                "desk/projects/unrelated-gardening.md",
                "memory/_always-load/commit-no-coauthor-trailer.md",
                "memory/_always-load/wake-on-ci-pattern.md",
            ])

    def test_exclude_dir_removes_a_subtree(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            rels = [p.relative_to(v).as_posix()
                    for p in wc.iter_markdown_paths(v, exclude_dirs=["_always-load"])]
            self.assertEqual(sorted(rels),
                             ["desk/projects/long-note.md", "desk/projects/unrelated-gardening.md"])

    def test_fingerprint_changes_when_a_note_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            paths = wc.iter_markdown_paths(v)
            before = wc.corpus_fingerprint(paths, v)
            self.assertEqual(before, wc.corpus_fingerprint(paths, v))
            time.sleep(0.01)
            (v / "desk/projects" / "unrelated-gardening.md").write_text(
                "Tomatoes and also basil.\n", encoding="utf-8")
            self.assertNotEqual(before, wc.corpus_fingerprint(paths, v))

    def test_title_includes_spaced_filename_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            rel, title, body = wc.read_document(
                v / "memory" / "_always-load" / "wake-on-ci-pattern.md", v)
            self.assertEqual(rel, "memory/_always-load/wake-on-ci-pattern.md")
            self.assertIn("wake on ci pattern", title)
            self.assertIn("kind: convention", body)


class TestLexicalSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_vault(self.tmp.name)
        self.conn, self.n = wc.build_lexical_index(
            self.vault, Path(self.tmp.name) / "lex.db")

    def tearDown(self):
        self.conn.close()   # sqlite3 close() is idempotent
        self.tmp.cleanup()

    def test_indexes_every_visible_note(self):
        self.assertEqual(self.n, 4)

    def test_ranks_the_note_that_actually_says_it_first(self):
        results, note = wc.search_lexical(self.conn, "Co-Authored-By trailer", k=3)
        self.assertIsNotNone(note, "hyphenated query should have needed sanitizing")
        self.assertEqual(results[0]["path"],
                         "memory/_always-load/commit-no-coauthor-trailer.md")

    def test_score_is_positive_and_larger_is_better(self):
        results, _ = wc.search_lexical(self.conn, "commit messages trailer", k=3)
        self.assertGreater(results[0]["score"], 0)
        self.assertGreaterEqual(results[0]["score"], results[-1]["score"])

    def test_finds_text_far_past_the_start_of_a_long_note(self):
        # The passage sits after ~3,400 characters of filler. A fixed leading
        # window would miss it; FTS5 indexes the whole document.
        results, _ = wc.search_lexical(self.conn, "mooring permit harbour", k=3)
        self.assertEqual(results[0]["path"], "desk/projects/long-note.md")

    def test_unmatched_query_returns_nothing_rather_than_guessing(self):
        results, _ = wc.search_lexical(self.conn, "quantum chromodynamics", k=5)
        self.assertEqual(results, [])

    def test_invalid_fts5_syntax_falls_back_instead_of_raising(self):
        results, note = wc.search_lexical(self.conn, 'trailer OR AND ")(', k=3)
        self.assertIn("not valid FTS5", note)
        self.assertTrue(results)

    def test_empty_query_is_reported_not_crashed(self):
        results, note = wc.search_lexical(self.conn, "   ", k=3)
        self.assertEqual(results, [])
        self.assertEqual(note, "empty query")

    def test_phrase_syntax_is_passed_through_untouched(self):
        results, note = wc.search_lexical(self.conn, '"git commit messages"', k=3)
        self.assertIsNone(note, "valid FTS5 should not be rewritten")
        self.assertEqual(results[0]["path"],
                         "memory/_always-load/commit-no-coauthor-trailer.md")

    def test_index_is_reused_when_the_corpus_is_unchanged(self):
        conn2, n2 = wc.build_lexical_index(self.vault, Path(self.tmp.name) / "lex.db")
        self.assertEqual(n2, 4)
        conn2.close()

    def test_index_rebuilds_when_a_note_changes(self):
        (self.vault / "desk/projects" / "new-note.md").write_text(
            "---\nkind: note\n---\nA brand new note.\n", encoding="utf-8")
        # setUp's connection is closed first: nothing in production holds a
        # second handle on an index that is being rebuilt, and leaving one open
        # here tests an arrangement that cannot occur while breaking on Windows,
        # where an open handle blocks deleting the file.
        self.conn.close()
        conn2, n2 = wc.build_lexical_index(self.vault, Path(self.tmp.name) / "lex.db")
        self.assertEqual(n2, 5)
        conn2.close()


@needs_numpy
class TestVectorSearchPlumbing(unittest.TestCase):
    """Stub embeddings are hash noise, so these prove wiring, never relevance."""

    def test_returns_ranked_paths_from_the_same_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            enc, doc_paths, vectors = wc.build_vector_index(
                v, Path(tmp) / "vec.npz", mode="stub")
            self.assertEqual(vectors.shape[1], 1024)
            self.assertEqual(len(doc_paths), vectors.shape[0])
            results, note = wc.search_vector(enc, doc_paths, vectors, "anything", k=3)
            self.assertIsNone(note)
            self.assertEqual(len(results), 3)
            known = set(p.relative_to(v).as_posix() for p in wc.iter_markdown_paths(v))
            for r in results:
                self.assertIn(r["path"], known)

    def test_one_row_per_document_after_max_passage_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            enc, doc_paths, vectors = wc.build_vector_index(
                v, Path(tmp) / "vec.npz", mode="stub")
            # The long note chunks into many rows; results must still name it once.
            self.assertGreater(len(doc_paths), 4)
            results, _ = wc.search_vector(enc, doc_paths, vectors, "anything", k=10)
            paths = [r["path"] for r in results]
            self.assertEqual(len(paths), len(set(paths)))
            self.assertEqual(len(paths), 4)

    def test_empty_query_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            enc, doc_paths, vectors = wc.build_vector_index(
                v, Path(tmp) / "vec.npz", mode="stub")
            results, note = wc.search_vector(enc, doc_paths, vectors, "", k=3)
            self.assertEqual((results, note), ([], "empty query"))


@needs_numpy
class TestVectorCacheIsIncremental(unittest.TestCase):
    """Only new or edited notes get embedded. A full rebuild is ~35 minutes.

    Each test counts the texts actually handed to the embedder, so "it reused
    the cache" is observed rather than inferred from wall-clock or from the
    cache file merely existing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_vault(self.tmp.name)
        self.cache = Path(self.tmp.name) / "vec.npz"
        self.embedded = []
        real = wc._load_embedder

        def counting(mode="local"):
            enc = real(mode)
            def wrapper(texts, batch_size=64):
                self.embedded.extend(texts)
                return enc(texts, batch_size=batch_size)
            return wrapper
        self._patch = counting
        self._real = real
        wc._load_embedder = counting

    def tearDown(self):
        wc._load_embedder = self._real
        self.tmp.cleanup()

    def _build(self):
        self.embedded = []
        return wc.build_vector_index(self.vault, self.cache, mode="stub")

    def test_first_build_embeds_every_note(self):
        _, doc_paths, vectors = self._build()
        self.assertEqual(len(self.embedded), vectors.shape[0])
        self.assertEqual(set(doc_paths), {
            "memory/_always-load/commit-no-coauthor-trailer.md",
            "memory/_always-load/wake-on-ci-pattern.md",
            "desk/projects/long-note.md",
            "desk/projects/unrelated-gardening.md"})

    def test_an_unchanged_corpus_embeds_nothing_the_second_time(self):
        _, _, first = self._build()
        _, doc_paths, second = self._build()
        self.assertEqual(self.embedded, [], "an unchanged vault must not re-embed")
        self.assertEqual(second.shape, first.shape)

    def test_a_touched_but_unedited_note_is_still_reused(self):
        self._build()
        # mtime moves, content does not — the cache keys on content, so this
        # must not trigger an embed. A Google-Drive sync does exactly this.
        p = self.vault / "desk/projects" / "unrelated-gardening.md"
        os.utime(p, (time.time() + 10, time.time() + 10))
        self._build()
        self.assertEqual(self.embedded, [])

    def test_only_the_edited_note_is_re_embedded(self):
        _, first_paths, _ = self._build()
        (self.vault / "desk/projects" / "unrelated-gardening.md").write_text(
            "---\nkind: note\n---\nTomatoes, basil, and a new thought.\n",
            encoding="utf-8")
        _, doc_paths, _ = self._build()
        self.assertTrue(self.embedded, "the edited note should have been embedded")
        self.assertTrue(
            all("unrelated-gardening" in t or "Tomatoes" in t for t in self.embedded),
            f"only the edited note should be embedded, got: {self.embedded}")
        self.assertEqual(len(doc_paths), len(first_paths))

    def test_a_new_note_is_embedded_and_the_rest_are_not(self):
        _, first_paths, _ = self._build()
        (self.vault / "desk/projects" / "fresh.md").write_text(
            "---\nkind: note\n---\nA brand new thought.\n", encoding="utf-8")
        _, doc_paths, _ = self._build()
        self.assertEqual(len(self.embedded), 1)
        self.assertIn("brand new thought", self.embedded[0])
        self.assertEqual(len(doc_paths), len(first_paths) + 1)
        self.assertIn("desk/projects/fresh.md", doc_paths)

    def test_a_deleted_notes_rows_are_dropped(self):
        self._build()
        (self.vault / "desk/projects" / "unrelated-gardening.md").unlink()
        _, doc_paths, vectors = self._build()
        self.assertEqual(self.embedded, [])
        self.assertNotIn("desk/projects/unrelated-gardening.md", doc_paths)
        self.assertEqual(vectors.shape[0], len(doc_paths))

    def test_rows_stay_aligned_with_their_documents_after_a_partial_rebuild(self):
        """The alignment invariant: reused rows are re-ordered ahead of new ones.

        Reused vectors are stacked first and freshly-embedded ones after, so
        doc_paths must be built in that same order. If it ever is not, search
        returns real similarity scores attached to the wrong note — the worst
        possible failure here, because nothing about the output looks broken.
        """
        import chunking
        self._build()
        (self.vault / "desk/projects" / "zebra.md").write_text(
            "---\nkind: note\n---\nZebras are striped.\n", encoding="utf-8")
        # Edit one existing note too, so the rebuild mixes all three cases:
        # reused rows, a re-embedded note, and a brand new one.
        (self.vault / "desk/projects" / "unrelated-gardening.md").write_text(
            "---\nkind: note\n---\nTomatoes, and now also rhubarb.\n", encoding="utf-8")
        enc, doc_paths, vectors = self._build()
        self.assertEqual(vectors.shape[0], len(doc_paths))

        # Check each row against the text it claims to represent. Stub
        # embeddings hash their input, so re-embedding a document's own chunks
        # reproduces its stored vectors exactly and any row/label skew shows up
        # as a row that matches none of them. Compared directly rather than
        # through `search_vector`, which strips its query — immaterial to a real
        # embedder, fatal to a byte-hash.
        import numpy as np
        for rel in sorted(set(doc_paths)):
            title, body = wc.read_document(self.vault / rel, self.vault)[1:]
            chunks = chunking.chunk_text(
                body, chunk_chars=wc.VECTOR_CHUNK_CHARS,
                overlap_chars=wc.VECTOR_CHUNK_OVERLAP) or [""]
            expected = wc._normalize(enc([f"{title}\n\n{c}" for c in chunks]))
            rows = [i for i, p in enumerate(doc_paths) if p == rel]
            self.assertEqual(len(rows), len(chunks),
                             f"{rel} has {len(rows)} rows but {len(chunks)} chunks")
            for i in rows:
                best = float(np.max(expected @ vectors[i]))
                self.assertAlmostEqual(
                    best, 1.0, places=3,
                    msg=f"row {i} is labelled {rel} but holds another note's vector")


class TestCallBudget(unittest.TestCase):
    """The ceiling is the experiment's control. It is enforced, not requested."""

    def _tmpdir(self):
        """A temp dir torn down by addCleanup rather than by a `with` block.

        Ordering is the whole point. addCleanup runs LIFO, so a daemon created
        after this call is closed *before* the directory is removed. A `with
        tempfile.TemporaryDirectory()` block cannot give that ordering — it
        unwinds when the method returns, which is before any cleanup runs, so on
        Windows the directory removal hits a SQLite handle that is still open
        and raises WinError 32 in place of whatever the test was asserting.
        """
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def _daemon(self, tmp, budget=6, arm="A", **kw):
        """A daemon that releases its SQLite handle when the test ends.

        Closed via addCleanup rather than at the end of the test body so that a
        failing assertion still releases the handle — otherwise the first
        failure in this class reports a teardown error instead of itself.
        """
        d = wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work",
                            arm=arm, call_budget=budget, verbose=False, **kw)
        self.addCleanup(d.close)
        return d

    def test_exactly_the_budget_is_served_and_the_next_call_is_refused(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp, budget=6)
        d.handle({"op": "begin", "question_id": "q1"})
        for expected_remaining in (5, 4, 3, 2, 1, 0):
            r = d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
            self.assertTrue(r["ok"])
            self.assertEqual(r["calls_remaining"], expected_remaining)
        refused = d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"], "budget_exhausted")
        self.assertIn("Answer now", refused["message"])

    def test_a_refused_call_does_not_consume_further_budget(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp, budget=1)
        d.handle({"op": "begin", "question_id": "q1"})
        d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
        for _ in range(4):
            d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
        self.assertEqual(d.handle({"op": "stats"})["calls_used"], 1)

    def test_begin_resets_the_budget_for_the_next_question(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp, budget=2)
        d.handle({"op": "begin", "question_id": "q1"})
        d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
        d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
        self.assertFalse(
            d.handle({"op": "search", "tool": "search_lexical", "query": "x"})["ok"])
        d.handle({"op": "begin", "question_id": "q2"})
        stats = d.handle({"op": "stats"})
        self.assertEqual((stats["calls_used"], stats["question_id"]), (0, "q2"))
        self.assertTrue(
            d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})["ok"])

    def test_arm_a_does_not_expose_the_vector_tool(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp)
        self.assertEqual(d.tools(), ["search_lexical"])
        d.handle({"op": "begin", "question_id": "q1"})
        r = d.handle({"op": "search", "tool": "search_vector", "query": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("unknown tool", r["error"])

    def test_a_rejected_tool_does_not_consume_budget(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp)
        d.handle({"op": "begin", "question_id": "q1"})
        d.handle({"op": "search", "tool": "search_vector", "query": "x"})
        self.assertEqual(d.handle({"op": "stats"})["calls_used"], 0)

    @needs_numpy
    def test_arm_b_exposes_both_tools(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp, arm="B", embed_mode="stub")
        self.assertEqual(d.tools(), ["search_lexical", "search_vector"])
        d.handle({"op": "begin", "question_id": "q1"})
        self.assertTrue(
            d.handle({"op": "search", "tool": "search_vector", "query": "x"})["ok"])

    def test_the_call_log_records_what_was_actually_searched(self):
        tmp = self._tmpdir()
        d = self._daemon(tmp)
        d.handle({"op": "begin", "question_id": "q1"})
        d.handle({"op": "search", "tool": "search_lexical", "query": "first try"})
        d.handle({"op": "search", "tool": "search_lexical", "query": "second try"})
        log = d.handle({"op": "stats"})["call_log"]
        self.assertEqual([c["query"] for c in log], ["first try", "second try"])
        self.assertEqual([c["n"] for c in log], [1, 2])


@unittest.skipUnless(hasattr(socket, "AF_UNIX"),
                     "the daemon's transport is a Unix domain socket")
class TestDaemonSocket(unittest.TestCase):
    def test_a_real_client_round_trip_over_the_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            run = Path(tempfile.mkdtemp(prefix="w1t-", dir="/tmp"))
            sock, ready = run / "s.sock", run / "ready"
            proc = subprocess.Popen(
                [sys.executable, str(_HERE / "week1_search_daemon.py"),
                 "--vault-path", str(vault), "--work-dir", str(Path(tmp) / "work"),
                 "--socket", str(sock), "--ready-file", str(ready), "--arm", "A"],
                stderr=subprocess.DEVNULL)
            try:
                deadline = time.time() + 60
                while not ready.exists():
                    self.assertIsNone(proc.poll(), "daemon exited before becoming ready")
                    self.assertLess(time.time(), deadline, "daemon never became ready")
                    time.sleep(0.05)
                info = wd.request(sock, {"op": "ping"})
                self.assertEqual(info["tools"], ["search_lexical"])
                self.assertEqual(info["n_docs"], 4)
                wd.request(sock, {"op": "begin", "question_id": "q1"})
                r = wd.request(sock, {"op": "search", "tool": "search_lexical",
                                      "query": "Co-Authored-By trailer", "k": 2})
                self.assertTrue(r["ok"])
                self.assertEqual(r["results"][0]["path"],
                                 "memory/_always-load/commit-no-coauthor-trailer.md")
            finally:
                try:
                    wd.request(sock, {"op": "shutdown"}, timeout=10)
                except Exception:
                    proc.kill()
                proc.wait(timeout=30)


class TestMcpShim(unittest.TestCase):
    """The shim is the arms' boundary: what it lists is what an arm can do."""

    def setUp(self):
        self._real = ws._daemon
        self.sent = []

    def tearDown(self):
        ws._daemon = self._real

    def _fake_daemon(self, tools, search_response):
        def fake(payload, timeout=180.0):
            self.sent.append(payload)
            if payload["op"] == "ping":
                return {"ok": True, "tools": tools}
            return search_response
        return fake

    def test_initialize_echoes_the_clients_protocol_version(self):
        resp = ws._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "week1")

    def test_notifications_get_no_reply(self):
        self.assertIsNone(ws._handle({"jsonrpc": "2.0",
                                      "method": "notifications/initialized"}))

    def test_arm_a_lists_only_the_lexical_tool(self):
        ws._daemon = self._fake_daemon(["search_lexical"], {})
        tools = ws._handle({"jsonrpc": "2.0", "id": 2,
                            "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["search_lexical"])

    def test_arm_b_lists_both_tools(self):
        ws._daemon = self._fake_daemon(["search_lexical", "search_vector"], {})
        tools = ws._handle({"jsonrpc": "2.0", "id": 2,
                            "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools],
                         ["search_lexical", "search_vector"])

    def test_tool_call_renders_results_with_remaining_budget(self):
        ws._daemon = self._fake_daemon(["search_lexical"], {
            "ok": True, "results": [{"path": "a/b.md", "score": 12.5, "snippet": "hi"}],
            "note": None, "calls_used": 1, "calls_remaining": 5})
        resp = ws._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "search_lexical",
                                      "arguments": {"query": "x"}}})
        text = resp["result"]["content"][0]["text"]
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("a/b.md", text)
        self.assertIn("5 tool calls remaining", text)

    def test_budget_exhaustion_reaches_the_model_as_an_error(self):
        ws._daemon = self._fake_daemon(["search_lexical"], {
            "ok": False, "error": "budget_exhausted", "message": "Answer now."})
        resp = ws._handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "search_lexical",
                                      "arguments": {"query": "x"}}})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Answer now.", resp["result"]["content"][0]["text"])

    def test_no_results_says_so_rather_than_returning_an_empty_block(self):
        ws._daemon = self._fake_daemon(["search_lexical"], {
            "ok": True, "results": [], "note": None, "calls_remaining": 4})
        resp = ws._handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                           "params": {"name": "search_lexical",
                                      "arguments": {"query": "x"}}})
        self.assertIn("No matching notes.", resp["result"]["content"][0]["text"])

    def test_unknown_method_returns_a_jsonrpc_error(self):
        resp = ws._handle({"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        self.assertEqual(resp["error"]["code"], -32601)


class TestSystemPrompt(unittest.TestCase):
    def test_arm_a_prompt_describes_one_tool(self):
        p = w1.build_system_prompt("A", 6)
        self.assertIn("search_lexical", p)
        self.assertNotIn("search_vector", p)

    def test_arm_b_prompt_describes_both(self):
        p = w1.build_system_prompt("B", 6)
        self.assertIn("search_lexical", p)
        self.assertIn("search_vector", p)

    def test_prompt_states_the_budget_and_the_no_answer_option(self):
        p = w1.build_system_prompt("A", 6)
        self.assertIn("at most 6 tool calls", p)
        self.assertIn("ANSWER: no answer found", p)


class TestJudgeCorrect(unittest.TestCase):
    """The negative stratum's trap: an empty answer looks correct however it arose."""

    def _negative(self):
        return ev.score_at_k([], [], k=5)

    def test_a_deliberate_no_answer_is_a_correct_rejection(self):
        self.assertEqual(w1.judge_correct(self._negative(), "no_answer", None),
                         (True, 1.0, 1.0))

    def test_a_reply_that_never_concluded_is_not_a_correct_rejection(self):
        self.assertEqual(w1.judge_correct(self._negative(), None, None),
                         (False, 0.0, 0.0))

    def test_a_timed_out_driver_is_not_a_correct_rejection(self):
        self.assertEqual(
            w1.judge_correct(self._negative(), "no_answer", "driver timed out after 300s"),
            (False, 0.0, 0.0))

    def test_naming_a_note_on_a_negative_question_is_wrong(self):
        score = ev.score_at_k([], ["something.md"], k=5)
        self.assertEqual(w1.judge_correct(score, "answer", None), (False, 0.0, 0.0))

    def test_an_ordinary_hit_keeps_the_scored_precision_and_recall(self):
        score = ev.score_at_k(["a.md"], ["a.md", "b.md"], k=5)
        self.assertEqual(w1.judge_correct(score, "answer", None), (True, 0.2, 1.0))

    def test_an_ordinary_miss_is_wrong(self):
        score = ev.score_at_k(["a.md"], ["x.md"], k=5)
        self.assertEqual(w1.judge_correct(score, "answer", None), (False, 0.0, 0.0))


class TestToolsetIsClosed(unittest.TestCase):
    """The arms differ by exactly one tool. Everything here defends that.

    `--disallowedTools` does not cover Claude Code's deferred surface: during
    this build the driver was talked into `ToolSearch` -> `Monitor` -> `grep -rl
    <vault>`, reading the corpus directly. An arm that can grep is not the arm
    the decision rule thinks it is comparing.
    """

    def test_the_denylist_covers_every_way_out_found_so_far(self):
        for tool in ("Bash", "Monitor", "Task", "Read", "Grep", "Glob",
                     "TaskOutput", "WebFetch", "Skill", "Workflow"):
            self.assertIn(tool, w1._DENIED_TOOLS)

    def test_only_arm_tools_and_the_loader_are_permitted(self):
        self.assertEqual(w1._HARNESS_TOOLS, {"ToolSearch"})
        self.assertEqual(w1._ARM_TOOLS,
                         {"mcp__week1__search_lexical", "mcp__week1__search_vector"})
        self.assertFalse(w1._ARM_TOOLS & set(w1._DENIED_TOOLS))
        self.assertFalse(w1._HARNESS_TOOLS & set(w1._DENIED_TOOLS))

    def test_the_settings_payload_carries_both_protections(self):
        """The two must ride together: hooks off AND the denylist applied."""
        settings = json.dumps({"disableAllHooks": True,
                               "permissions": {"deny": w1._DENIED_TOOLS}})
        parsed = json.loads(settings)
        self.assertTrue(parsed["disableAllHooks"])
        self.assertIn("Monitor", parsed["permissions"]["deny"])

    def _classify(self, tools_used):
        """The transcript audit, as run_driver_claude computes it."""
        return (sum(1 for t in tools_used if t in w1._ARM_TOOLS),
                sorted(set(tools_used) - w1._ARM_TOOLS - w1._HARNESS_TOOLS))

    def test_toolsearch_does_not_count_against_the_budget(self):
        """It loads the deferred schema; charging for it would cost a real search.

        Observed live: the daemon served 2 searches while the transcript held 3
        tool_use blocks, the third being ToolSearch.
        """
        counted, escapes = self._classify(
            ["ToolSearch", "mcp__week1__search_lexical", "mcp__week1__search_lexical"])
        self.assertEqual(counted, 2)
        self.assertEqual(escapes, [])

    def test_a_shell_capable_tool_is_reported_as_an_escape(self):
        counted, escapes = self._classify(
            ["ToolSearch", "Monitor", "mcp__week1__search_lexical"])
        self.assertEqual(counted, 1)
        self.assertEqual(escapes, ["Monitor"])

    def test_a_tool_nobody_has_thought_of_yet_is_still_an_escape(self):
        """The audit must not depend on the denylist being complete.

        A denylist enumerates known tools and goes stale as Claude Code adds
        them; this check asks the opposite question — was anything called that
        is not one of ours — so a brand-new tool fails the run on first use.
        """
        counted, escapes = self._classify(
            ["mcp__week1__search_lexical", "SomeToolShippedNextRelease"])
        self.assertNotIn("SomeToolShippedNextRelease", w1._DENIED_TOOLS)
        self.assertEqual(escapes, ["SomeToolShippedNextRelease"])

    def test_a_second_mcp_server_would_be_an_escape(self):
        _, escapes = self._classify(["mcp__othersrv__read_vault"])
        self.assertEqual(escapes, ["mcp__othersrv__read_vault"])

    def test_arm_a_is_not_given_the_vector_tool_in_its_prompt(self):
        self.assertNotIn("search_vector", w1.build_system_prompt("A", 6))


class TestAggregation(unittest.TestCase):
    def _rows(self):
        return [
            {"stratum": "distinctive-token", "p_at_5": 0.2, "r_at_5": 1.0,
             "correct": True, "tool_calls": 2, "is_negative": False,
             "id": "d1", "question": "q", "expected": ["a.md"], "answered": ["a.md"],
             "driver_error": None},
            {"stratum": "distinctive-token", "p_at_5": 0.0, "r_at_5": 0.0,
             "correct": False, "tool_calls": 6, "is_negative": False,
             "id": "d2", "question": "q", "expected": ["b.md"], "answered": ["z.md"],
             "driver_error": None},
            {"stratum": "negative", "p_at_5": 1.0, "r_at_5": 1.0,
             "correct": True, "tool_calls": 3, "is_negative": True,
             "id": "n1", "question": "q", "expected": [], "answered": [],
             "driver_error": None},
        ]

    def test_bucket_averages_are_per_question(self):
        b = w1._blank_bucket()
        for row in self._rows()[:2]:
            w1._accumulate(b, row)
        out = w1._finalize(b)
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["p_at_5"], 0.1)
        self.assertEqual(out["r_at_5"], 0.5)
        self.assertEqual(out["accuracy"], 0.5)
        self.assertEqual(out["mean_tool_calls"], 4.0)
        self.assertNotIn("correct_rejection_rate", out)

    def test_negative_stratum_reports_a_rejection_rate(self):
        b = w1._blank_bucket()
        w1._accumulate(b, self._rows()[2])
        self.assertEqual(w1._finalize(b)["correct_rejection_rate"], 1.0)

    def test_miss_reasons_name_the_actual_failure(self):
        rows = self._rows()
        self.assertEqual(w1._miss_reason(rows[1]), "answered with the wrong note(s)")
        gave_up = dict(rows[1], answered=[])
        self.assertIn("gave up", w1._miss_reason(gave_up))
        false_positive = dict(rows[2], correct=False, answered=["x.md"])
        self.assertIn("should have found nothing", w1._miss_reason(false_positive))
        errored = dict(rows[1], driver_error="timed out")
        self.assertIn("timed out", w1._miss_reason(errored))

    def test_table_shows_every_stratum_and_the_miss_list(self):
        report = {"arm": "A", "driver": "mock", "n_questions": 3,
                  "corpus": {"n_docs": 8592},
                  "overall": {"n": 3, "p_at_5": 0.4, "r_at_5": 0.667, "accuracy": 0.667,
                              "mean_tool_calls": 3.67},
                  "per_stratum": {
                      "distinctive-token": {"n": 2, "p_at_5": 0.1, "r_at_5": 0.5,
                                            "accuracy": 0.5, "mean_tool_calls": 4.0},
                      "negative": {"n": 1, "p_at_5": 1.0, "r_at_5": 1.0,
                                   "accuracy": 1.0, "mean_tool_calls": 3.0}},
                  "misses": [{"id": "d2", "stratum": "distinctive-token",
                              "question": "q", "expected": ["b.md"],
                              "answered": ["z.md"], "tool_calls": 6,
                              "reason": "answered with the wrong note(s)"}]}
        table = w1.render_table(report)
        self.assertIn("distinctive-token", table)
        self.assertIn("negative", table)
        self.assertIn("OVERALL", table)
        self.assertIn("MISSES (1 of 3)", table)
        self.assertIn("d2", table)

    def test_table_says_none_when_nothing_missed(self):
        report = {"arm": "A", "driver": "mock", "n_questions": 1,
                  "corpus": {"n_docs": 1},
                  "overall": {"n": 1, "p_at_5": 0.2, "r_at_5": 1.0, "accuracy": 1.0,
                              "mean_tool_calls": 1.0},
                  "per_stratum": {}, "misses": []}
        self.assertIn("none", w1.render_table(report))


class TestSmokeFixture(unittest.TestCase):
    def test_the_shipped_smoke_set_is_a_valid_gold_set(self):
        p = _HERE / "fixtures" / "week1-gold" / "smoke-set.json"
        entries = w1.load_gold_set(p)
        self.assertEqual(len(entries), 8)
        strata = set(e["stratum"] for e in entries)
        self.assertEqual(strata, {"distinctive-token", "pure-paraphrase",
                                  "episodic-temporal", "research-density", "negative"})
        negatives = [e for e in entries if e["stratum"] == "negative"]
        self.assertTrue(all(e["expected_note_paths"] == [] for e in negatives))

    def test_the_smoke_set_is_labelled_as_throwaway(self):
        p = _HERE / "fixtures" / "week1-gold" / "smoke-set.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("NOT THE GOLD SET", " ".join(doc["_comment"]))


class TestSocketPathLength(unittest.TestCase):
    def test_run_dir_leaves_room_for_the_socket_path(self):
        d = w1._make_run_dir("A")
        try:
            self.assertLessEqual(len(str(d / "search.sock").encode()), 100)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Rank penalty
# ---------------------------------------------------------------------------

_FRAGMENT_NOTE = (
    "---\nkind: preferences\nstatus: inbox\nslug: never-cache-1\n"
    "mining_confidence: LOW\n---\n"
    "User stated: ...never cache an absolute vault path as a literal, resolve it\n"
)
_REAL_NOTE = (
    "---\nkind: convention\nstatus: active\ntags: [vault, paths]\n"
    "aliases: [resolve the vault path at runtime]\n---\n"
    "# Vault path convention\n\nResolve vault paths at runtime; never cache one.\n"
)


class TestClassifyDocument(unittest.TestCase):
    """Every detection path, asserted against hand-written expectations.

    The flag sets below are written out by hand from what each fixture is, not
    read back off the classifier — a check that asks the implementation what it
    thinks and then agrees with it verifies nothing.
    """

    def test_body_opening_with_a_miner_lead_in_is_a_fragment(self):
        for opener in ("User stated:", "Fix observed:", "User corrected the agent:"):
            raw = f"---\nkind: fix\n---\n{opener} ...something clipped mid-sentence\n"
            self.assertEqual(wc.classify_document("memory/_inbox/x.md", raw),
                             {"fragment"}, opener)

    def test_mining_frontmatter_is_a_fragment_even_without_the_lead_in(self):
        raw = "---\nkind: idea\nmining_confidence: HIGH\n---\nA perfectly ordinary body.\n"
        self.assertEqual(wc.classify_document("memory/_inbox/y.md", raw), {"fragment"})

    def test_mid_word_slug_under_a_miner_directory_is_a_fragment(self):
        raw = "---\nkind: idea\n---\nrver's vault-hardwiring can't degrade\n"
        self.assertEqual(
            wc.classify_document("memory/idea/rver-s-vault-hardwiring-can-t-1.md", raw),
            {"fragment"})

    def test_an_ellipsis_opener_under_a_miner_directory_is_a_fragment(self):
        raw = "---\nkind: idea\n---\n...processed in order. This is useful for bridges\n"
        self.assertEqual(
            wc.classify_document("memory/idea/processed-in-order-this-is-useful-1.md",
                                 raw),
            {"fragment"})

    def test_a_real_looking_slug_outside_a_miner_directory_is_not_a_fragment(self):
        raw = "---\nkind: convention\nstatus: active\n---\nOrdinary prose.\n"
        self.assertEqual(wc.classify_document("memory/_always-load/ps-tooling.md", raw),
                         set())

    def test_a_short_real_word_does_not_read_as_a_truncation(self):
        self.assertFalse(wc._looks_truncated_slug("api-key-handling"))
        self.assertFalse(wc._looks_truncated_slug("read-multi-agent-memory"))
        self.assertTrue(wc._looks_truncated_slug("rver-s-vault"))
        self.assertTrue(wc._looks_truncated_slug("10-leaf-to-the-tree"))

    def test_each_penalized_status_is_flagged(self):
        for status in ("unfiled", "inbox", "superseded", "expired"):
            raw = f"---\nkind: convention\nstatus: {status}\n---\n# Real note\n\nProse.\n"
            self.assertEqual(wc.classify_document("memory/x.md", raw),
                             {"status"}, status)

    def test_an_active_note_carries_no_class(self):
        self.assertEqual(wc.classify_document("desk/projects/a/design.md", _REAL_NOTE), set())

    def test_a_note_can_carry_several_classes(self):
        self.assertEqual(wc.classify_document("memory/_inbox/never-cache-1.md",
                                              _FRAGMENT_NOTE),
                         {"fragment", "status"})

    def test_a_dream_staging_proposal_is_its_own_class(self):
        raw = "# Proposal 132: inbox_merge / merge\n\nnever-cache-1.md and never-cache.md\n"
        self.assertEqual(
            wc.classify_document(
                "desk/scratch/inbox-20260712/132-inbox_merge-merge.proposal.md", raw),
            {"staging"})

    def test_a_proposal_outside_dream_staging_is_not_staged(self):
        raw = "# Proposal 132: something\n\nbody\n"
        self.assertEqual(wc.classify_document("desk/projects/x/proposal.md", raw), set())


class TestShapeRuleGatedOnStatus(unittest.TestCase):
    """232 of the 234 notes in `personal/preferences/` are `status: active` AND
    fragment-shaped, because the promotion pipeline promoted the fragment's body
    verbatim. Demoting them for their shape is the penalty's one real cost, and
    the gold set cannot see it — no gold target lives there. So it is pinned
    here instead."""

    PROMOTED_FRAGMENT = (
        "---\nkind: preferences\nstatus: active\nslug: always-absolute\n---\n"
        "User stated: ...share file paths, always absolute\n"
    )

    def test_a_promoted_fragment_is_classed_apart_from_an_unfiled_one(self):
        self.assertEqual(
            wc.classify_document("memory/preferences/always-absolute.md",
                                 self.PROMOTED_FRAGMENT),
            {"fragment-promoted"})
        self.assertEqual(
            wc.classify_document("memory/_inbox/never-cache-1.md", _FRAGMENT_NOTE),
            {"fragment", "status"})

    def test_every_promoted_status_gates_the_shape_rule(self):
        for status in sorted(wc.PROMOTED_STATUSES):
            raw = (f"---\nkind: fix\nstatus: {status}\n---\n"
                   "Fix observed: ...something clipped\n")
            self.assertEqual(wc.classify_document("memory/fix/x.md", raw),
                             {"fragment-promoted"}, status)

    def test_the_recommended_weights_spare_a_promoted_fragment(self):
        flags = wc.classify_document("memory/preferences/always-absolute.md",
                                     self.PROMOTED_FRAGMENT)
        self.assertEqual(
            wc.penalty_multiplier(flags, wc.DEFAULT_PENALTY_WEIGHTS), 1.0,
            "the recommended shape must leave a filed note's score untouched")

    def test_the_as_measured_weights_demote_it(self):
        flags = wc.classify_document("memory/preferences/always-absolute.md",
                                     self.PROMOTED_FRAGMENT)
        self.assertAlmostEqual(
            wc.penalty_multiplier(flags, wc.AS_MEASURED_PENALTY_WEIGHTS), 0.30)

    def test_the_two_presets_differ_only_on_promoted_fragments(self):
        """Anything not carrying `fragment-promoted` must score identically under
        both presets — otherwise the twelve committed scorecards stop being
        reproducible from the preset that claims to reproduce them."""
        for flags in ({"fragment"}, {"status"}, {"staging"}, {"fragment", "status"},
                      {"staging", "status"}, set()):
            self.assertEqual(
                wc.penalty_multiplier(frozenset(flags), wc.DEFAULT_PENALTY_WEIGHTS),
                wc.penalty_multiplier(frozenset(flags), wc.AS_MEASURED_PENALTY_WEIGHTS),
                flags)

    def test_an_unfiled_fragment_is_still_demoted_under_both(self):
        flags = wc.classify_document("memory/_inbox/x.md", _FRAGMENT_NOTE)
        for preset in (wc.DEFAULT_PENALTY_WEIGHTS, wc.AS_MEASURED_PENALTY_WEIGHTS):
            self.assertLess(wc.penalty_multiplier(flags, preset), 0.3)

    def test_the_class_set_covers_everything_the_classifier_emits(self):
        """A weight aimed at a class name that does not exist is a silent no-op,
        so the CLI validates against this set. It has to stay complete."""
        emitted = set()
        for rel, raw in (
            ("memory/_inbox/a.md", _FRAGMENT_NOTE),
            ("memory/preferences/b.md", self.PROMOTED_FRAGMENT),
            ("desk/scratch/x/1.proposal.md", "# Proposal 1: merge\n\nbody\n"),
            ("memory/c.md", "---\nstatus: expired\n---\n# Real\n\nProse.\n"),
        ):
            emitted |= wc.classify_document(rel, raw)
        self.assertEqual(emitted, set(wc.PENALTY_CLASSES))


class TestPenaltyMultiplier(unittest.TestCase):
    def test_no_flags_leaves_a_score_untouched(self):
        self.assertEqual(wc.penalty_multiplier(frozenset(), {"fragment": 0.3}), 1.0)

    def test_classes_compound(self):
        self.assertAlmostEqual(
            wc.penalty_multiplier(frozenset({"fragment", "status"}),
                                  {"fragment": 0.5, "status": 0.5}),
            0.25)

    def test_an_unweighted_class_is_a_no_op(self):
        self.assertEqual(
            wc.penalty_multiplier(frozenset({"staging"}), {"fragment": 0.1}), 1.0)


def _make_penalty_vault(tmp):
    """A vault where the fragments outrank the real note on a plain BM25 query.

    Four fragments repeat the query's vocabulary in a short body, which is what
    BM25 rewards; the real answer says the same thing once inside a longer note.
    This is the shape the live vault has, reproduced small enough to assert on.
    """
    v = Path(tmp) / "vault"
    (v / "memory" / "_inbox").mkdir(parents=True)
    (v / "memory" / "_always-load").mkdir(parents=True)
    for i in range(4):
        (v / "memory" / "_inbox" / f"never-cache-{i}.md").write_text(
            "---\nkind: preferences\nstatus: inbox\nmining_confidence: LOW\n---\n"
            "User stated: ...vault path vault path never cache the vault path\n",
            encoding="utf-8")
    (v / "memory" / "_always-load" / "vault-path-convention.md").write_text(
        "---\nkind: convention\nstatus: active\n---\n"
        "# Vault path convention\n\n" + ("Unrelated background prose. " * 60)
        + "\nResolve the vault path at runtime.\n",
        encoding="utf-8")
    return v


class TestRankPenalty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_penalty_vault(self.tmp.name)
        self.conn, self.n = wc.build_lexical_index(
            self.vault, Path(self.tmp.name) / "lex.db")
        self.flags = wc.load_doc_flags(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_flags_are_stored_for_the_fragments_only(self):
        self.assertEqual(
            set(self.flags),
            {f"memory/_inbox/never-cache-{i}.md" for i in range(4)})
        self.assertEqual(self.flags["memory/_inbox/never-cache-0.md"],
                         frozenset({"fragment", "status"}))

    def test_without_the_penalty_the_fragments_bury_the_real_note(self):
        results, _ = wc.search_lexical(self.conn, "vault path", k=3)
        self.assertTrue(all(r["path"].startswith("memory/_inbox/") for r in results),
                        [r["path"] for r in results])

    def test_the_penalty_lifts_the_real_note_to_the_top(self):
        results, _ = wc.search_lexical(
            self.conn, "vault path", k=3, doc_flags=self.flags,
            penalty={"fragment": 0.3, "status": 0.6})
        self.assertEqual(results[0]["path"],
                         "memory/_always-load/vault-path-convention.md")

    def test_demoted_notes_are_still_returned(self):
        """The whole point. A demoted note ranks lower; it does not disappear."""
        results, _ = wc.search_lexical(
            self.conn, "vault path", k=5, doc_flags=self.flags,
            penalty={"fragment": 0.3, "status": 0.6})
        self.assertEqual(len(results), 5)
        self.assertEqual(
            {r["path"] for r in results if r["path"].startswith("memory/_inbox/")},
            {f"memory/_inbox/never-cache-{i}.md" for i in range(4)})

    def test_a_penalized_note_that_is_the_only_match_still_comes_back_first(self):
        results, _ = wc.search_lexical(
            self.conn, "mining_confidence", k=5, doc_flags=self.flags,
            penalty={"fragment": 0.01, "status": 0.01})
        self.assertTrue(results)
        self.assertTrue(results[0]["path"].startswith("memory/_inbox/"))

    def test_the_adjusted_score_and_the_reason_are_both_reported(self):
        results, _ = wc.search_lexical(
            self.conn, "vault path", k=5, doc_flags=self.flags,
            penalty={"fragment": 0.5, "status": 0.5})
        demoted = next(r for r in results if r["path"].startswith("memory/_inbox/"))
        self.assertEqual(demoted["penalty"], "fragment,status")
        self.assertAlmostEqual(demoted["score"], round(demoted["raw_score"] * 0.25, 4),
                               places=3)

    def test_an_unpenalized_search_is_byte_identical_to_before_the_change(self):
        """The control has to stay the control: passing no penalty must not
        reorder, rescore, or annotate anything."""
        plain, _ = wc.search_lexical(self.conn, "vault path", k=5)
        with_flags_but_no_penalty, _ = wc.search_lexical(
            self.conn, "vault path", k=5, doc_flags=self.flags)
        self.assertEqual(plain, with_flags_but_no_penalty)
        self.assertTrue(all("penalty" not in r and "raw_score" not in r for r in plain))

    def test_the_penalty_can_promote_a_note_from_outside_the_top_k(self):
        """Over-fetch, not re-sort-the-five. A penalty that only reordered the
        k rows already returned could never surface the note the fragments were
        hiding, which is the only thing it exists to do."""
        results, _ = wc.search_lexical(
            self.conn, "vault path", k=1, doc_flags=self.flags,
            penalty={"fragment": 0.3, "status": 0.6})
        self.assertEqual(results[0]["path"],
                         "memory/_always-load/vault-path-convention.md")


class TestLexicalVariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_vault(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, variant):
        return wc.build_lexical_index(
            self.vault, Path(self.tmp.name) / wc.lexical_db_name(variant),
            variant=variant)

    def test_every_variant_builds_and_indexes_the_same_notes(self):
        for variant in sorted(wc.LEXICAL_VARIANTS):
            conn, n = self._build(variant)
            try:
                self.assertEqual(n, 4, variant)
            finally:
                conn.close()

    def test_the_baseline_variant_is_what_the_first_run_used(self):
        """Pinned to literals, because the follow-up's whole attribution rests on
        the baseline already carrying porter stemming and a 4x title weight."""
        spec = wc.LEXICAL_VARIANTS["baseline"]
        self.assertEqual(spec["tokenize"], "porter unicode61")
        self.assertEqual(spec["weights"], (0.0, 4.0, 1.0))
        self.assertEqual(wc.lexical_db_name("baseline"), "lexical.db")

    def test_porter_stemming_matches_an_inflected_query(self):
        conn, _ = self._build("baseline")
        try:
            results, _ = wc.search_lexical(conn, "watering tomatoes", k=3)
            self.assertEqual(results[0]["path"], "desk/projects/unrelated-gardening.md")
        finally:
            conn.close()

    def test_the_plain_variant_drops_stemming(self):
        conn, _ = self._build("plain")
        try:
            results, _ = wc.search_lexical(conn, "tomato", k=3,
                                           weights=wc.LEXICAL_VARIANTS["plain"]["weights"])
            self.assertEqual(results, [],
                             "unicode61 alone should not match 'tomatoes' from 'tomato'")
        finally:
            conn.close()

    def test_the_fields_variant_indexes_tags_into_their_own_column(self):
        conn, _ = self._build("fields")
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(docs)").fetchall()]
            self.assertEqual(cols, ["path", "title", "meta", "body"])
            row = conn.execute(
                "SELECT meta FROM docs WHERE path = ?",
                ("memory/_always-load/commit-no-coauthor-trailer.md",)).fetchone()
            self.assertEqual(row[0], "git commits")
        finally:
            conn.close()

    def test_switching_variant_rebuilds_rather_than_reusing_the_other_schema(self):
        db = Path(self.tmp.name) / "shared.db"
        conn_a, _ = wc.build_lexical_index(self.vault, db, variant="baseline")
        conn_a.close()
        conn_b, n = wc.build_lexical_index(self.vault, db, variant="fields")
        try:
            cols = [r[1] for r in conn_b.execute("PRAGMA table_info(docs)").fetchall()]
            self.assertIn("meta", cols)
            self.assertEqual(n, 4)
        finally:
            conn_b.close()

    def test_an_unknown_variant_is_refused(self):
        with self.assertRaises(wc.CorpusError):
            wc.variant_spec("porter-plus-magic")


class TestExtractMetaText(unittest.TestCase):
    def test_aliases_and_tags_are_flattened(self):
        head = "kind: convention\naliases: [resolve at runtime, never cache]\ntags: [vault]"
        self.assertEqual(wc.extract_meta_text(head),
                         "resolve at runtime never cache vault")

    def test_absent_fields_give_an_empty_string(self):
        self.assertEqual(wc.extract_meta_text("kind: convention\nstatus: active"), "")


class TestWindowsFileHandles(unittest.TestCase):
    """Two paths that only execute on Windows, exercised here so they are not
    written blind and re-broken by the next edit.

    POSIX lets you unlink a file another handle still holds open; Windows raises
    WinError 32. Every SQLite handle in this module is therefore a resource that
    has to be given back explicitly, and the rebuild path has to work without
    deleting the file. Both were broken until the first PR to run this suite on
    a Windows runner said so.
    """

    def test_the_daemon_releases_its_sqlite_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work", verbose=False)
            self.assertIsNotNone(d.conn)
            d.close()
            self.assertIsNone(d.conn)

    def test_closing_the_daemon_twice_is_harmless(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work", verbose=False)
            d.close()
            d.close()

    def test_the_daemon_works_as_a_context_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            with wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work",
                                 verbose=False) as d:
                self.assertTrue(d.handle({"op": "ping"})["ok"])
            self.assertIsNone(d.conn)

    def test_a_rebuild_survives_an_undeletable_index(self):
        """The Windows case: unlink raises, so the rebuild drops the tables
        through the handle it has instead. The rebuilt index must be complete,
        not merely non-crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            db = Path(tmp) / "lex.db"
            conn1, n1 = wc.build_lexical_index(vault, db)
            conn1.close()
            self.assertEqual(n1, 4)

            (vault / "desk/projects" / "new-note.md").write_text(
                "---\nkind: note\n---\nA brand new note about mooring.\n",
                encoding="utf-8")

            real_unlink = Path.unlink
            calls = []

            def refuse(self, *a, **kw):
                calls.append(self)
                raise PermissionError(32, "used by another process")

            Path.unlink = refuse
            try:
                conn2, n2 = wc.build_lexical_index(vault, db)
            finally:
                Path.unlink = real_unlink
            try:
                self.assertTrue(calls, "the rebuild never tried to unlink")
                self.assertEqual(n2, 5, "the rebuilt index is missing the new note")
                results, _ = wc.search_lexical(conn2, "mooring", k=3)
                self.assertEqual(results[0]["path"], "desk/projects/new-note.md")
                # The docflags table has to come back too, or a penalized run
                # silently serves an unpenalized ranking.
                self.assertEqual(
                    conn2.execute("SELECT count(*) FROM docflags").fetchone()[0], 0)
            finally:
                conn2.close()


class TestConstructorReleasesTheHandleItOpened(unittest.TestCase):
    """A constructor that raises after opening SQLite has to close it itself.

    Nothing else can. `__init__` opens the FTS5 connection first and builds the
    vector index second, so a failure in the second half never returns the
    object — a `with` block or a `contextlib.closing()` at the call site is
    handed nothing to close. The connection then survives until the cyclic GC
    finalizes it at an arbitrary later moment, which on Python 3.13+ prints
    `ResourceWarning: unclosed database` into whatever stderr is being captured
    at that moment. Seven leaked from here landed in the wrong test's captured
    output and failed `test_notify_enabled_absent_by_default` in
    `scripts/test_agentm_config.py` on the macOS runner, a file that does not
    import this one.

    This forces the failure with a mock rather than by removing numpy, because
    the `needs_numpy` skip guards mean an arm-B daemon is never built when numpy
    is missing — the suite stopped observing the leak without the leak being
    fixed. A real arm-B run on such a machine still hits it, so the trigger has
    to be the constructor failing, not the environment that made it fail.
    """

    def test_a_failed_vector_build_does_not_leak_the_lexical_connection(self):
        opened = []
        real_build = wd.week1_corpus.build_lexical_index

        def spy(*a, **kw):
            conn, n_docs = real_build(*a, **kw)
            opened.append(conn)
            return conn, n_docs

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(wd.week1_corpus, "build_lexical_index", spy), \
                 mock.patch.object(
                     wd.week1_corpus, "build_vector_index",
                     side_effect=ModuleNotFoundError("No module named 'numpy'")):
                with self.assertRaises(ModuleNotFoundError):
                    wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work",
                                    arm="B", verbose=False)

            self.assertEqual(len(opened), 1,
                             "the lexical index was never built, so this test "
                             "proves nothing about releasing its handle")
            # Asked of the connection itself rather than of the daemon, which was
            # never handed back. A closed sqlite3 connection refuses work; an
            # open one answers.
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")


class TestOrJoinQuery(unittest.TestCase):
    """FTS5's bare MATCH ANDs every term. These pin what the OR rewrite does to
    a query, because the rewrite is the difference between a paraphrase finding
    nothing and finding something."""

    def test_bare_words_are_quoted_and_or_joined(self):
        self.assertEqual(wc.or_join_query("house project ideas"),
                         '"house" OR "project" OR "ideas"')

    def test_a_quoted_phrase_survives_as_a_phrase(self):
        self.assertEqual(wc.or_join_query('"F0" prompt pack'),
                         '"F0" OR "prompt" OR "pack"')
        self.assertEqual(wc.or_join_query('"rebuild not refactor" blog draft'),
                         '"rebuild not refactor" OR "blog" OR "draft"')

    def test_an_operator_the_agent_wrote_is_not_searched_for(self):
        self.assertEqual(wc.or_join_query('"first rewrite" OR "ground-up rewrite"'),
                         '"first rewrite" OR "ground-up rewrite"')

    def test_punctuation_cannot_become_syntax(self):
        self.assertEqual(wc.or_join_query("v5.3 what's foo-bar"),
                         '"v5" OR "3" OR "what" OR "s" OR "foo" OR "bar"')

    def test_an_empty_or_wordless_query_gives_nothing_to_search(self):
        self.assertIsNone(wc.or_join_query(""))
        self.assertIsNone(wc.or_join_query("   "))
        self.assertIsNone(wc.or_join_query("!!! ???"))


class TestQueryModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_vault(self.tmp.name)
        self.conn, _ = wc.build_lexical_index(self.vault, Path(self.tmp.name) / "lex.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_as_is_ands_the_terms_and_can_return_nothing(self):
        """The 2026-08-06 behaviour, pinned: no note holds all four words, so
        the default mode returns an empty set rather than its best partial match."""
        results, _ = wc.search_lexical(
            self.conn, "tomatoes harbour trailer speculatively", k=5)
        self.assertEqual(results, [])

    def test_or_mode_returns_the_best_partial_match_instead_of_nothing(self):
        results, note = wc.search_lexical(
            self.conn, "tomatoes harbour trailer speculatively", k=5,
            query_mode="or")
        self.assertTrue(results)
        self.assertIn("any of the query's terms", note)

    def test_or_mode_still_ranks_the_note_that_matches_most_terms_first(self):
        results, _ = wc.search_lexical(
            self.conn, "tomatoes full sun watering", k=4, query_mode="or")
        self.assertEqual(results[0]["path"], "desk/projects/unrelated-gardening.md")

    def test_or_mode_leaves_a_single_term_query_alone(self):
        as_is, _ = wc.search_lexical(self.conn, "tomatoes", k=3)
        or_mode, note = wc.search_lexical(self.conn, "tomatoes", k=3, query_mode="or")
        self.assertEqual([r["path"] for r in as_is], [r["path"] for r in or_mode])
        self.assertIsNone(note)

    def test_or_mode_composes_with_the_penalty(self):
        flags = wc.load_doc_flags(self.conn)
        results, _ = wc.search_lexical(
            self.conn, "tomatoes harbour", k=5, query_mode="or",
            doc_flags=flags, penalty={"fragment": 0.3})
        self.assertTrue(results)


class TestPenaltyCliGuards(unittest.TestCase):
    """A zero weight is exclusion in a demotion's costume — the CLI refuses it."""

    def _main(self, penalty):
        return w1.main([
            "--gold-set", str(_HERE / "fixtures" / "week1-gold" / "smoke-set.json"),
            "--arm", "A", "--driver", "mock", "--penalty", penalty,
            "--vault-path", str(_make_vault(tempfile.mkdtemp())),
        ])

    def test_a_zero_weight_is_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self._main('{"fragment": 0}')
        self.assertIn("exclusion", str(cm.exception))

    def test_a_weight_above_one_is_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self._main('{"fragment": 1.5}')
        self.assertIn("(0, 1]", str(cm.exception))

    def test_an_unknown_class_is_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self._main('{"fragmnet": 0.3}')
        self.assertIn("unknown penalty class", str(cm.exception))


class TestSurfaceStats(unittest.TestCase):
    def test_flagged_share_counts_served_rows_not_questions(self):
        rows = [
            {"tool_call_log": [
                {"result_penalties": ["fragment", "", "fragment,status", "", ""]},
                {"result_penalties": ["", ""]},
            ]},
            {"tool_call_log": [{"result_penalties": ["staging"]}]},
        ]
        stats = w1._surface_stats(rows)
        self.assertEqual(stats["results_served"], 8)
        self.assertEqual(stats["flagged_results_served"], 3)
        self.assertEqual(stats["flagged_share"], 0.375)
        self.assertEqual(stats["flagged_by_class"],
                         {"fragment": 2, "staging": 1, "status": 1})

    def test_a_run_that_served_nothing_reports_no_share_rather_than_zero(self):
        self.assertIsNone(w1._surface_stats([])["flagged_share"])


GOLD_TWO_ENTRIES = {
    "entries": [
        {"id": "q1", "question": "where is the vault path convention?",
         "expected_note_paths": ["personal/_always-load/vault-path.md"],
         "stratum": "distinctive-token", "source": "transcript"},
        {"id": "q2", "question": "nothing answers this",
         "expected_note_paths": [], "stratum": "negative", "source": "transcript"},
    ]
}


class TestExpectedPathPrefix(unittest.TestCase):
    """One labeled set, two corpus roots.

    Labels are relative to the agent tree; the 2026-08-10 cutover moved the
    corpus root one level above it. Every expected path below is written out by
    hand rather than built by calling the prefix logic being tested.
    """

    def _gold_file(self, td: str) -> Path:
        p = Path(td) / "gold.json"
        p.write_text(json.dumps(GOLD_TWO_ENTRIES), encoding="utf-8")
        return p

    def test_no_prefix_leaves_paths_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            entries = w1.load_gold_set(self._gold_file(td))
        self.assertEqual(entries[0]["expected_note_paths"],
                         ["personal/_always-load/vault-path.md"])

    def test_prefix_is_prepended(self):
        with tempfile.TemporaryDirectory() as td:
            entries = w1.load_gold_set(self._gold_file(td), "Agent")
        self.assertEqual(entries[0]["expected_note_paths"],
                         ["Agent/personal/_always-load/vault-path.md"])

    def test_prefix_slashes_do_not_double_up(self):
        with tempfile.TemporaryDirectory() as td:
            entries = w1.load_gold_set(self._gold_file(td), "/Agent/")
        self.assertEqual(entries[0]["expected_note_paths"],
                         ["Agent/personal/_always-load/vault-path.md"])

    def test_negative_stratum_stays_empty_under_a_prefix(self):
        """An empty expected list means 'no note answers this' — prefixing it
        must not invent a path, or the negative stratum stops being negative."""
        with tempfile.TemporaryDirectory() as td:
            entries = w1.load_gold_set(self._gold_file(td), "Agent")
        self.assertEqual(entries[1]["expected_note_paths"], [])


class TestMissingPrefixHint(unittest.TestCase):
    """A whole-set miss is a root mismatch, and the hint has to say which way."""

    def _vault(self, td: str, *rel: str) -> Path:
        v = Path(td)
        for r in rel:
            p = v / r
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        return v

    def test_names_the_subtree_that_would_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            v = self._vault(td, "Agent/personal/a.md", "Agent/personal/b.md")
            hint = w1.hint_for_missing_prefix(
                ["personal/a.md", "personal/b.md"], v, "")
        self.assertIn("--expected-path-prefix Agent", hint)

    def test_tells_you_to_drop_a_prefix_that_should_not_be_there(self):
        with tempfile.TemporaryDirectory() as td:
            v = self._vault(td, "personal/a.md")
            hint = w1.hint_for_missing_prefix(["Agent/personal/a.md"], v, "Agent")
        self.assertIn("Drop --expected-path-prefix", hint)

    def test_real_drift_is_not_reported_as_a_root_mismatch(self):
        """No single top-level directory resolves them — that is label drift,
        and calling it a prefix problem would send the reader the wrong way."""
        with tempfile.TemporaryDirectory() as td:
            v = self._vault(td, "Agent/personal/a.md", "Other/thing.md")
            hint = w1.hint_for_missing_prefix(
                ["memory/a.md", "memory/gone.md"], v, "")
        self.assertIn("real label drift", hint)
        self.assertNotIn("--expected-path-prefix Agent", hint)

    def test_no_missing_paths_produces_no_hint(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(w1.hint_for_missing_prefix([], Path(td), ""), "")


if __name__ == "__main__":
    unittest.main()
