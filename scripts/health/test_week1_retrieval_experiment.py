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
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts", _REPO / "harness" / "skills" / "memory" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import eval_v6_retrieval as ev  # noqa: E402
import week1_corpus as wc  # noqa: E402
import week1_retrieval_experiment as w1  # noqa: E402
import week1_search_daemon as wd  # noqa: E402
import week1_search_shim as ws  # noqa: E402


def _make_vault(tmp):
    """A five-note vault with hand-known contents, so rankings can be asserted."""
    v = Path(tmp) / "vault"
    (v / "personal" / "_always-load").mkdir(parents=True)
    (v / "projects").mkdir(parents=True)
    notes = {
        "personal/_always-load/commit-no-coauthor-trailer.md":
            "---\nkind: convention\ntags: [git, commits]\n---\n"
            "Do not append a Co-Authored-By trailer to git commit messages.\n",
        "personal/_always-load/wake-on-ci-pattern.md":
            "---\nkind: convention\n---\n"
            "Never mark a task done speculatively. Wait for the check suite.\n",
        "projects/unrelated-gardening.md":
            "---\nkind: note\n---\nTomatoes want full sun and deep watering.\n",
        "projects/long-note.md":
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
                "personal/_always-load/commit-no-coauthor-trailer.md",
                "personal/_always-load/wake-on-ci-pattern.md",
                "projects/long-note.md",
                "projects/unrelated-gardening.md",
            ])

    def test_exclude_dir_removes_a_subtree(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            rels = [p.relative_to(v).as_posix()
                    for p in wc.iter_markdown_paths(v, exclude_dirs=["_always-load"])]
            self.assertEqual(sorted(rels),
                             ["projects/long-note.md", "projects/unrelated-gardening.md"])

    def test_fingerprint_changes_when_a_note_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            paths = wc.iter_markdown_paths(v)
            before = wc.corpus_fingerprint(paths, v)
            self.assertEqual(before, wc.corpus_fingerprint(paths, v))
            time.sleep(0.01)
            (v / "projects" / "unrelated-gardening.md").write_text(
                "Tomatoes and also basil.\n", encoding="utf-8")
            self.assertNotEqual(before, wc.corpus_fingerprint(paths, v))

    def test_title_includes_spaced_filename_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _make_vault(tmp)
            rel, title, body = wc.read_document(
                v / "personal" / "_always-load" / "wake-on-ci-pattern.md", v)
            self.assertEqual(rel, "personal/_always-load/wake-on-ci-pattern.md")
            self.assertIn("wake on ci pattern", title)
            self.assertIn("kind: convention", body)


class TestLexicalSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = _make_vault(self.tmp.name)
        self.conn, self.n = wc.build_lexical_index(
            self.vault, Path(self.tmp.name) / "lex.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_indexes_every_visible_note(self):
        self.assertEqual(self.n, 4)

    def test_ranks_the_note_that_actually_says_it_first(self):
        results, note = wc.search_lexical(self.conn, "Co-Authored-By trailer", k=3)
        self.assertIsNotNone(note, "hyphenated query should have needed sanitizing")
        self.assertEqual(results[0]["path"],
                         "personal/_always-load/commit-no-coauthor-trailer.md")

    def test_score_is_positive_and_larger_is_better(self):
        results, _ = wc.search_lexical(self.conn, "commit messages trailer", k=3)
        self.assertGreater(results[0]["score"], 0)
        self.assertGreaterEqual(results[0]["score"], results[-1]["score"])

    def test_finds_text_far_past_the_start_of_a_long_note(self):
        # The passage sits after ~3,400 characters of filler. A fixed leading
        # window would miss it; FTS5 indexes the whole document.
        results, _ = wc.search_lexical(self.conn, "mooring permit harbour", k=3)
        self.assertEqual(results[0]["path"], "projects/long-note.md")

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
                         "personal/_always-load/commit-no-coauthor-trailer.md")

    def test_index_is_reused_when_the_corpus_is_unchanged(self):
        conn2, n2 = wc.build_lexical_index(self.vault, Path(self.tmp.name) / "lex.db")
        self.assertEqual(n2, 4)
        conn2.close()

    def test_index_rebuilds_when_a_note_changes(self):
        (self.vault / "projects" / "new-note.md").write_text(
            "---\nkind: note\n---\nA brand new note.\n", encoding="utf-8")
        conn2, n2 = wc.build_lexical_index(self.vault, Path(self.tmp.name) / "lex.db")
        self.assertEqual(n2, 5)
        conn2.close()


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
            "personal/_always-load/commit-no-coauthor-trailer.md",
            "personal/_always-load/wake-on-ci-pattern.md",
            "projects/long-note.md",
            "projects/unrelated-gardening.md"})

    def test_an_unchanged_corpus_embeds_nothing_the_second_time(self):
        _, _, first = self._build()
        _, doc_paths, second = self._build()
        self.assertEqual(self.embedded, [], "an unchanged vault must not re-embed")
        self.assertEqual(second.shape, first.shape)

    def test_a_touched_but_unedited_note_is_still_reused(self):
        self._build()
        # mtime moves, content does not — the cache keys on content, so this
        # must not trigger an embed. A Google-Drive sync does exactly this.
        p = self.vault / "projects" / "unrelated-gardening.md"
        os.utime(p, (time.time() + 10, time.time() + 10))
        self._build()
        self.assertEqual(self.embedded, [])

    def test_only_the_edited_note_is_re_embedded(self):
        _, first_paths, _ = self._build()
        (self.vault / "projects" / "unrelated-gardening.md").write_text(
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
        (self.vault / "projects" / "fresh.md").write_text(
            "---\nkind: note\n---\nA brand new thought.\n", encoding="utf-8")
        _, doc_paths, _ = self._build()
        self.assertEqual(len(self.embedded), 1)
        self.assertIn("brand new thought", self.embedded[0])
        self.assertEqual(len(doc_paths), len(first_paths) + 1)
        self.assertIn("projects/fresh.md", doc_paths)

    def test_a_deleted_notes_rows_are_dropped(self):
        self._build()
        (self.vault / "projects" / "unrelated-gardening.md").unlink()
        _, doc_paths, vectors = self._build()
        self.assertEqual(self.embedded, [])
        self.assertNotIn("projects/unrelated-gardening.md", doc_paths)
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
        (self.vault / "projects" / "zebra.md").write_text(
            "---\nkind: note\n---\nZebras are striped.\n", encoding="utf-8")
        # Edit one existing note too, so the rebuild mixes all three cases:
        # reused rows, a re-embedded note, and a brand new one.
        (self.vault / "projects" / "unrelated-gardening.md").write_text(
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

    def _daemon(self, tmp, budget=6):
        return wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work",
                               arm="A", call_budget=budget, verbose=False)

    def test_exactly_the_budget_is_served_and_the_next_call_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp, budget=1)
            d.handle({"op": "begin", "question_id": "q1"})
            d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
            for _ in range(4):
                d.handle({"op": "search", "tool": "search_lexical", "query": "commit"})
            self.assertEqual(d.handle({"op": "stats"})["calls_used"], 1)

    def test_begin_resets_the_budget_for_the_next_question(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            self.assertEqual(d.tools(), ["search_lexical"])
            d.handle({"op": "begin", "question_id": "q1"})
            r = d.handle({"op": "search", "tool": "search_vector", "query": "x"})
            self.assertFalse(r["ok"])
            self.assertIn("unknown tool", r["error"])

    def test_a_rejected_tool_does_not_consume_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d.handle({"op": "begin", "question_id": "q1"})
            d.handle({"op": "search", "tool": "search_vector", "query": "x"})
            self.assertEqual(d.handle({"op": "stats"})["calls_used"], 0)

    def test_arm_b_exposes_both_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wd.SearchDaemon(_make_vault(tmp), Path(tmp) / "work", arm="B",
                                embed_mode="stub", verbose=False)
            self.assertEqual(d.tools(), ["search_lexical", "search_vector"])
            d.handle({"op": "begin", "question_id": "q1"})
            self.assertTrue(
                d.handle({"op": "search", "tool": "search_vector", "query": "x"})["ok"])

    def test_the_call_log_records_what_was_actually_searched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d.handle({"op": "begin", "question_id": "q1"})
            d.handle({"op": "search", "tool": "search_lexical", "query": "first try"})
            d.handle({"op": "search", "tool": "search_lexical", "query": "second try"})
            log = d.handle({"op": "stats"})["call_log"]
            self.assertEqual([c["query"] for c in log], ["first try", "second try"])
            self.assertEqual([c["n"] for c in log], [1, 2])


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
                                 "personal/_always-load/commit-no-coauthor-trailer.md")
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


if __name__ == "__main__":
    unittest.main()
