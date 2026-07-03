#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/notes_link_discovery.py (V4 #43).

The engine lives in the memory skill dir; its tests live here in scripts/ so CI
(`cd scripts && python3 -m unittest discover -p 'test_*.py'`) runs them. We add
the skill scripts dir to sys.path to import the module under test.

Covers the plan's task-1 verification matrix:
  (a) two notes sharing distinctive terms        -> surfaced, with shared terms
  (b) two unrelated notes                         -> not surfaced
  (c) an AgentMemory/-style entry in the fixture  -> excluded from the corpus
  (d) an already-[[linked]] related pair          -> not re-suggested
  (e) common-vocabulary notes                     -> not over-surfaced (IDF works)
plus --format json structure + read-only (fixtures byte-identical after a run).
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import notes_link_discovery as nld  # noqa: E402


def _write(root: Path, rel: str, body: str, fm: dict | None = None) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    if fm is not None:
        lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
        text += f"---\n{lines}\n---\n\n"
    text += body
    p.write_text(text, encoding="utf-8")
    return p


class _Vault:
    """A temp dir that LOOKS like an Obsidian root (carries `.obsidian/`)."""
    def __enter__(self) -> Path:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / ".obsidian").mkdir(parents=True)
        return self.root

    def __exit__(self, *exc):
        self._td.cleanup()


def _pairset(suggestions) -> set:
    return {frozenset((s.a_rel, s.b_rel)) for s in suggestions}


def _has_pair(suggestions, a: str, b: str) -> bool:
    return frozenset((a, b)) in _pairset(suggestions)


class TestRelatedPairSurfaced(unittest.TestCase):
    def test_distinctive_overlap_surfaced_with_terms(self):
        # (a) two notes sharing distinctive terms -> surfaced as a related pair.
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism is a covenant ordinance. The sacrament renews the "
                   "covenant made at baptism through the priesthood ordinance.")
            _write(v, "Church/confirmation.md",
                   "Confirmation follows baptism as a covenant ordinance. The "
                   "priesthood confers the gift through this sacred ordinance.")
            # Filler so the corpus isn't degenerate (df band needs room).
            _write(v, "Tech/python.md",
                   "Asyncio coroutines drive the event loop scheduling tasks.")
            _write(v, "Tech/rust.md",
                   "The borrow checker enforces ownership and lifetime rules.")
            _write(v, "Home/recipe.md",
                   "Roast vegetables with olive oil garlic and rosemary slowly.")
            _notes, sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertTrue(_has_pair(sugg, "Church/baptism", "Church/confirmation"),
                            f"expected baptism<->confirmation, got {_pairset(sugg)}")
            pair = next(s for s in sugg
                        if frozenset((s.a_rel, s.b_rel))
                        == frozenset(("Church/baptism", "Church/confirmation")))
            self.assertTrue(set(pair.shared_terms) & {"covenant", "ordinance", "sacrament"},
                            f"expected covenant/ordinance/sacrament, got {pair.shared_terms}")
            self.assertTrue(pair.same_folder)

    def test_unrelated_not_surfaced(self):
        # (b) two unrelated notes -> not surfaced (no distinctive shared terms).
        with _Vault() as v:
            _write(v, "Tech/python.md",
                   "Asyncio coroutines drive the event loop scheduling tasks await.")
            _write(v, "Home/recipe.md",
                   "Roast vegetables with olive oil garlic and rosemary slowly.")
            _write(v, "Church/baptism.md",
                   "Baptism is a covenant ordinance renewed by the sacrament.")
            _notes, sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertFalse(_has_pair(sugg, "Tech/python", "Home/recipe"),
                             f"unrelated pair surfaced: {_pairset(sugg)}")


    def test_small_corpus_band_does_not_collapse(self):
        # Regression (adversarial review): with 3 notes, the old
        # `max_df = max(min_df, int(ratio*n))` floored max_df to min_df, collapsing
        # the df band to a single point. A distinctive term mentioned by a third
        # note (df=3) was dropped and the vocab could collapse to empty, losing a
        # genuinely related pair. The cap must be disabled for sub-`min_df/ratio`
        # corpora so IDF (not a degenerate hard cap) governs.
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "covenant ordinance sacrament. baptism renews the covenant ordinance.")
            _write(v, "Church/confirmation.md",
                   "covenant ordinance sacrament. confirmation confers the ordinance gift.")
            _write(v, "Church/sunday.md",
                   "covenant ordinance sacrament mentioned once among many sunday topics.")
            notes = nld.build_corpus(v)
            model = nld.build_model(notes)
            self.assertTrue(model.idf, "vocab collapsed to empty on a 3-note corpus")
            _notes, sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertTrue(_has_pair(sugg, "Church/baptism", "Church/confirmation"),
                            f"genuine pair lost to band collapse: {_pairset(sugg)}")


class TestAgentMemoryExcluded(unittest.TestCase):
    def test_agent_vault_entry_never_in_corpus(self):
        # (c) the agent's own vault subfolder is excluded even if it shares
        # terms — R0.10: the exclusion is now derived at runtime from the
        # `vault` argument's own directory name (was a hardcoded "AgentMemory"
        # literal, the retired pre-V5-3 vault root name), so this fixture
        # nests the vault ONE LEVEL below the Obsidian root (as it is in
        # production — e.g. `<Obsidian>/Agent/` alongside sibling personal
        # notes) rather than flattening vault == Obsidian root.
        with _Vault() as obsidian_root:
            _write(obsidian_root, "Church/baptism.md",
                   "Baptism is a covenant ordinance renewed weekly by the sacrament.")
            _write(obsidian_root, "Church/confirmation.md",
                   "Confirmation is a covenant ordinance conferring the sacrament gift.")
            # An agent entry that shares the SAME distinctive vocabulary — must
            # still never appear as a source or target (hard domain boundary).
            _write(obsidian_root, "Agent/personal/agent-note.md",
                   "Covenant ordinance sacrament covenant ordinance sacrament.",
                   fm={"kind": "convention", "status": "active", "created": "2026-05-29"})
            vault = obsidian_root / "Agent"
            notes, sugg = nld.discover(vault, min_score=0.05, top=40)
            rels = {n.rel for n in notes}
            self.assertNotIn("personal/agent-note", rels,
                             "agent vault entry leaked into the corpus")
            for s in sugg:
                self.assertNotIn("agent-note", s.a_rel)
                self.assertNotIn("agent-note", s.b_rel)

    def test_obsidian_config_dir_excluded(self):
        with _Vault() as v:
            _write(v, ".obsidian/plugins/notes.md",
                   "Covenant ordinance sacrament covenant ordinance sacrament.")
            _write(v, "Church/baptism.md",
                   "Baptism is a covenant ordinance renewed by the sacrament.")
            notes, _sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertTrue(all(".obsidian" not in n.rel for n in notes))


class TestAlreadyLinkedFiltered(unittest.TestCase):
    def test_already_linked_pair_not_resuggested(self):
        # (d) a related pair that already wikilinks -> filtered out.
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism is a covenant ordinance. See [[confirmation]] for the "
                   "covenant ordinance sacrament follow-on.")
            _write(v, "Church/confirmation.md",
                   "Confirmation is a covenant ordinance conferring the sacrament gift.")
            _write(v, "Tech/python.md",
                   "Asyncio coroutines drive the event loop scheduling tasks.")
            _notes, sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertFalse(_has_pair(sugg, "Church/baptism", "Church/confirmation"),
                             "already-linked pair was re-suggested")

    def test_path_style_link_filtered(self):
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism covenant ordinance sacrament. See [[Church/confirmation]].")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance sacrament gift.")
            _write(v, "Tech/python.md",
                   "Asyncio coroutines event loop scheduling.")
            _notes, sugg = nld.discover(v, min_score=0.05, top=40)
            self.assertFalse(_has_pair(sugg, "Church/baptism", "Church/confirmation"))


class TestCommonVocabularyNotOverSurfaced(unittest.TestCase):
    def test_boilerplate_words_dont_connect(self):
        # (e) notes sharing only ubiquitous boilerplate -> not surfaced; IDF +
        # the max-df cap drop the common terms so only genuine overlap connects.
        with _Vault() as v:
            # Six notes all share "meeting attend schedule" boilerplate, each with
            # its own distinctive single-note content.
            boiler = "Meeting attend schedule weekly regular ongoing gathering."
            _write(v, "Home/a.md", boiler + " Distinct apricot marmalade canning.")
            _write(v, "Home/b.md", boiler + " Distinct bicycle derailleur tuning.")
            _write(v, "Home/c.md", boiler + " Distinct ceramic kiln glazing.")
            _write(v, "Home/d.md", boiler + " Distinct drywall spackle sanding.")
            _write(v, "Home/e.md", boiler + " Distinct espresso tamper grind.")
            _write(v, "Home/f.md", boiler + " Distinct ferment kombucha scoby.")
            _notes, sugg = nld.discover(v, min_score=0.18, top=40)
            # None of these should connect to each other on boilerplate alone.
            self.assertEqual(_pairset(sugg), set(),
                             f"boilerplate over-surfaced: {_pairset(sugg)}")

    def test_genuine_overlap_beats_boilerplate(self):
        # Same boilerplate, but two notes ALSO share distinctive terms -> only
        # that pair surfaces.
        with _Vault() as v:
            boiler = "Meeting attend schedule weekly regular ongoing gathering."
            _write(v, "Home/a.md", boiler + " Sourdough starter hydration levain crumb.")
            _write(v, "Home/b.md", boiler + " Sourdough starter hydration levain crumb bake.")
            _write(v, "Home/c.md", boiler + " Distinct ceramic kiln glazing technique.")
            _write(v, "Home/d.md", boiler + " Distinct drywall spackle sanding primer.")
            _write(v, "Home/e.md", boiler + " Distinct espresso tamper grind dose.")
            _notes, sugg = nld.discover(v, min_score=0.18, top=40)
            self.assertEqual(_pairset(sugg), {frozenset(("Home/a", "Home/b"))},
                             f"expected only sourdough pair, got {_pairset(sugg)}")


class TestJsonAndCli(unittest.TestCase):
    def test_json_structure(self):
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism covenant ordinance renewed by the sacrament weekly.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament gift.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = nld.main(["--vault", str(v), "--format", "json", "--min-score", "0.05"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("notes", data)
            self.assertIn("suggestions", data)
            self.assertGreaterEqual(data["notes"], 3)
            self.assertTrue(data["suggestions"], "expected at least one suggestion")
            s0 = data["suggestions"][0]
            for key in ("a", "b", "a_title", "b_title", "score", "shared_terms", "signal"):
                self.assertIn(key, s0)
            self.assertEqual(s0["signal"], "tfidf")
            self.assertIsInstance(s0["score"], float)

    def test_read_only_fixtures_unchanged(self):
        with _Vault() as v:
            paths = [
                _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance renewed by the sacrament."),
                _write(v, "Church/confirmation.md",
                       "Confirmation covenant ordinance conferring the sacrament."),
            ]
            before = {p: p.read_bytes() for p in paths}
            nld.discover(v, min_score=0.05, top=40)
            for p in paths:
                self.assertEqual(p.read_bytes(), before[p],
                                 f"engine modified a personal note: {p}")


class TestReport(unittest.TestCase):
    def test_report_has_summary_sections_and_paste_links(self):
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism covenant ordinance renewed by the sacrament weekly.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament gift.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            notes, sugg = nld.discover(v, min_score=0.05, top=40)
            report = nld.build_report(notes, sugg, today="2026-05-29")
            self.assertIn("# Personal-notes link suggestions — 2026-05-29", report)
            self.assertIn("suggestion(s) across", report)
            self.assertIn("## Shared-vocabulary links (TF-IDF)", report)
            self.assertIn("shared terms:", report)
            # Paste-ready bidirectional links present.
            self.assertIn("[[confirmation]]", report)
            self.assertIn("[[baptism]]", report)
            self.assertIn("paste into", report)

    def test_report_empty_suggestions(self):
        with _Vault() as v:
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            _write(v, "Home/recipe.md", "Roast vegetables olive oil garlic rosemary.")
            notes, sugg = nld.discover(v, min_score=0.9, top=40)
            report = nld.build_report(notes, sugg, today="2026-05-29")
            self.assertIn("No related-but-unlinked pairs", report)
            self.assertNotIn("## Shared-vocabulary links", report)

    def test_report_ambiguous_stem_uses_full_path(self):
        # Two distinct notes share a stem -> paste-link must use the full rel path.
        with _Vault() as v:
            _write(v, "Work/daily.md", "Standup blockers velocity sprint backlog burndown.")
            _write(v, "Journal/daily.md", "Standup blockers velocity sprint backlog burndown.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            notes, sugg = nld.discover(v, min_score=0.05, top=40)
            report = nld.build_report(notes, sugg, today="2026-05-29")
            # The ambiguous stem must be disambiguated by path, not bare.
            self.assertIn("[[Work/daily]]", report)
            self.assertIn("[[Journal/daily]]", report)
            self.assertNotIn("[[daily]]", report)

    def test_report_bracketed_name_not_a_broken_wikilink(self):
        # Note names with [] can't be a valid [[wikilink]] target — the report
        # must flag them, not emit a broken `[[… - [date]]]`.
        with _Vault() as v:
            _write(v, "Church/Leadership Meeting - [28 Feb 2016].md",
                   "Branch presidency assignments baptism confirmations welfare visits.")
            _write(v, "Church/Leadership Meeting - [21 Feb 2016].md",
                   "Branch presidency assignments baptism confirmations welfare visits.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            notes, sugg = nld.discover(v, min_score=0.05, top=40)
            report = nld.build_report(notes, sugg, today="2026-05-29")
            self.assertNotIn("[28 Feb 2016]]]", report)
            self.assertNotIn("[21 Feb 2016]]]", report)
            self.assertIn("link via Obsidian's `[[` picker", report)

    def test_default_report_path_is_meta(self):
        p = nld.default_report_path(Path("/tmp/AgentMemory"), "2026-05-29")
        self.assertEqual(p, Path("/tmp/AgentMemory/_meta/notes-links-2026-05-29.md"))

    def test_report_out_refuses_personal_note(self):
        # Adversarial review: `--out <personal note>` must be REFUSED, never
        # overwrite the operator's note (hard DC-1 guarantee).
        with _Vault() as v:
            victim = _write(v, "Church/baptism.md",
                            "Baptism covenant ordinance renewed by the sacrament.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            before = victim.read_bytes()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = nld.main(["--vault", str(v), "--report", "--min-score", "0.05",
                               "--out", str(victim)])
            self.assertEqual(rc, 2)
            self.assertEqual(victim.read_bytes(), before,
                             "--out clobbered a personal note")

    def test_report_out_refuses_outside_vault(self):
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism covenant ordinance renewed by the sacrament.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            with tempfile.TemporaryDirectory() as other:
                target = Path(other) / "escape.md"
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = nld.main(["--vault", str(v), "--report",
                                   "--min-score", "0.05", "--out", str(target)])
                self.assertEqual(rc, 2)
                self.assertFalse(target.exists(), "report escaped the vault")

    def test_is_safe_report_path_unit(self):
        vault = Path("/tmp/AgentMemory")
        ok = vault / "_meta" / "notes-links-2026-05-29.md"
        self.assertTrue(nld.is_safe_report_path(ok, vault, set()))
        outside = Path("/tmp/Obsidian/Church/note.md")
        self.assertFalse(nld.is_safe_report_path(outside, vault, set()))
        # A corpus member inside the vault is still refused.
        note = vault / "personal" / "note.md"
        self.assertFalse(nld.is_safe_report_path(note, vault, {note.resolve()}))

    def test_report_cli_writes_only_to_meta(self):
        # The --report run writes exactly one file under _meta/ and leaves every
        # personal note byte-identical (read-only guarantee, the one write).
        with _Vault() as v:
            paths = [
                _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance renewed by the sacrament."),
                _write(v, "Church/confirmation.md",
                       "Confirmation covenant ordinance conferring the sacrament."),
            ]
            before = {p: p.read_bytes() for p in paths}
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = nld.main(["--vault", str(v), "--report", "--min-score", "0.05"])
            self.assertEqual(rc, 0)
            report_path = v / "_meta" / f"notes-links-{date.today().isoformat()}.md"
            self.assertTrue(report_path.exists(), "report not written to _meta/")
            self.assertIn(str(report_path), buf.getvalue())
            for p in paths:
                self.assertEqual(p.read_bytes(), before[p],
                                 f"--report modified a personal note: {p}")
            # No stray files outside _meta/ and the two fixtures.
            md_files = sorted(q.relative_to(v).as_posix() for q in v.rglob("*.md"))
            self.assertIn("_meta/" + report_path.name, md_files)


class TestEmbeddingSignal(unittest.TestCase):
    def _note(self, rel, title="", body="", links=None):
        folder = rel.split("/")[0] if "/" in rel else ""
        return nld.Note(path=Path(rel + ".md"), rel=rel, title=title or rel,
                        folder=folder, created="", updated="", body=body,
                        links=set(links or ()))

    def test_score_embedding_pairs_threshold(self):
        # Parallel unit vectors -> cosine 1.0 (surfaced); orthogonal -> 0 (not).
        notes = [self._note("A/one"), self._note("B/two"), self._note("C/three")]
        vectors = {
            "A/one":   nld._normalize([1.0, 0.0, 0.0]),
            "B/two":   nld._normalize([0.96, 0.28, 0.0]),   # ~cos 0.96 with one
            "C/three": nld._normalize([0.0, 0.0, 1.0]),     # orthogonal to both
        }
        sugg = nld.score_embedding_pairs(notes, vectors, min_score=0.7, top=40)
        keys = {frozenset((s.a_rel, s.b_rel)) for s in sugg}
        self.assertIn(frozenset(("A/one", "B/two")), keys)
        self.assertNotIn(frozenset(("A/one", "C/three")), keys)
        self.assertTrue(all(s.signal == "embedding" for s in sugg))
        self.assertTrue(all(s.shared_terms == [] for s in sugg))

    def test_embedding_surfaces_pair_tfidf_misses(self):
        # Two notes that are SEMANTICALLY related but share no distinctive terms:
        # TF-IDF finds nothing; injected near-parallel vectors -> embedding finds
        # them. Proves the embedding layer adds coverage.
        with _Vault() as v:
            _write(v, "Home/car.md", "Sold the sedan to a dealership downtown today.")
            _write(v, "Home/auto.md", "Traded my vehicle at the lot this afternoon.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling tasks.")
            notes = nld.build_corpus(v)
            # TF-IDF: the two car notes share no distinctive terms.
            _n, tfidf = nld.discover(v, min_score=0.18, top=40)
            tf_keys = {frozenset((s.a_rel, s.b_rel)) for s in tfidf}
            self.assertNotIn(frozenset(("Home/car", "Home/auto")), tf_keys)
            # Embedding: inject near-parallel vectors for the two car notes.
            vectors = {n.rel: nld._normalize([1.0, 0.0]) for n in notes}
            vectors["Home/auto"] = nld._normalize([0.99, 0.14])
            vectors["Tech/python"] = nld._normalize([0.0, 1.0])
            emb = nld.score_embedding_pairs(notes, vectors, min_score=0.7, top=40)
            emb_keys = {frozenset((s.a_rel, s.b_rel)) for s in emb}
            self.assertIn(frozenset(("Home/car", "Home/auto")), emb_keys)

    def test_embed_corpus_graceful_skip(self):
        # When embed_text raises EmbeddingUnavailable, the whole signal is None.
        import embed as embed_mod
        notes = [self._note("A/one", body="x"), self._note("B/two", body="y")]
        orig = embed_mod.embed_text
        try:
            def boom(text, mode=None):
                raise embed_mod.EmbeddingUnavailable("no model")
            embed_mod.embed_text = boom
            self.assertIsNone(nld.embed_corpus(notes, mode="local", cache_path=None))
            self.assertIsNone(nld.discover_embeddings(notes, mode="local"))
        finally:
            embed_mod.embed_text = orig

    def test_embed_corpus_caches_by_hash(self):
        # Stub mode is deterministic; the 2nd run must hit the cache (0 embeds).
        import embed as embed_mod
        with _Vault() as v:
            _write(v, "Home/a.md", "Apricot marmalade canning jars.")
            _write(v, "Home/b.md", "Bicycle derailleur cassette tuning.")
            notes = nld.build_corpus(v)
            cache = v / "_meta" / "notes-embeddings.json"
            calls = {"n": 0}
            orig = embed_mod.embed_text
            try:
                def counting(text, mode=None):
                    calls["n"] += 1
                    return orig(text, mode="stub")
                embed_mod.embed_text = counting
                nld.embed_corpus(notes, mode="stub", cache_path=cache)
                first = calls["n"]
                self.assertEqual(first, len(notes))
                self.assertTrue(cache.exists())
                nld.embed_corpus(notes, mode="stub", cache_path=cache)
                self.assertEqual(calls["n"], first, "2nd run re-embedded despite cache")
            finally:
                embed_mod.embed_text = orig

    def test_embed_corpus_rejects_stale_dimension_cache(self):
        # Adversarial review: the content-hash cache key omits the embedding
        # model identity, so a cache written under an OLD model dimension (the
        # documented EMBEDDING_DIM 384->1024 upgrade path) is silently reused for
        # unchanged notes while changed/new notes re-embed at the NEW dimension.
        # `_cosine_unit` zips the two and truncates to the shorter length, so a
        # mismatched-dim pair scores 1.0 and surfaces as a bogus high-confidence
        # suggestion. embed_corpus must NOT return mixed-dimension vectors;
        # vec_index.py guards exactly this (it rejects dim mismatches) -- this
        # cache must too (invalidate stale-dim entries, never reuse them).
        import embed as embed_mod
        with _Vault() as v:
            _write(v, "Home/a.md", "alpha content one")
            _write(v, "Home/b.md", "beta content two")
            notes = nld.build_corpus(v)
            cache = v / "_meta" / "notes-embeddings.json"
            orig = embed_mod.embed_text
            try:
                # Run 1: OLD model emits 3-d vectors -> cache holds 3-d.
                embed_mod.embed_text = lambda t, mode=None: [1.0, 0.0, 0.0]
                nld.embed_corpus(notes, mode="stub", cache_path=cache)
                # One note changes; operator has upgraded to a NEW (wider) model.
                _write(v, "Home/b.md", "beta content two CHANGED")
                notes2 = nld.build_corpus(v)
                embed_mod.embed_text = lambda t, mode=None: [1.0, 0.0, 0.0] + [0.0] * 5
                vectors = nld.embed_corpus(notes2, mode="stub", cache_path=cache)
            finally:
                embed_mod.embed_text = orig
            dims = {len(vec) for vec in vectors.values()}
            self.assertEqual(
                len(dims), 1,
                f"embed_corpus returned mixed-dimension vectors {sorted(dims)}: "
                f"a stale-dim cache entry was reused after a model/dim change; "
                f"score_embedding_pairs then cosines mismatched-length vectors "
                f"via zip() truncation -> garbage/false-positive scores")

    def test_embed_index_path_is_separate_from_vec_index(self):
        p = nld.default_embed_index_path(Path("/tmp/AgentMemory"))
        self.assertEqual(p, Path("/tmp/AgentMemory/_meta/notes-embeddings.json"))
        self.assertNotEqual(p.name, "vec-index.db")

    def test_report_dual_signal_sections(self):
        notes = [self._note("Home/car", title="Car sale"),
                 self._note("Home/auto", title="Auto trade"),
                 self._note("Church/baptism", title="Baptism",
                            body="covenant ordinance sacrament"),
                 self._note("Church/confirmation", title="Confirmation",
                            body="covenant ordinance sacrament")]
        tfidf = [nld.Suggestion(
            a_rel="Church/baptism", b_rel="Church/confirmation",
            a_title="Baptism", b_title="Confirmation",
            a_folder="Church", b_folder="Church", score=0.5,
            shared_terms=["covenant", "ordinance"], same_folder=True)]
        emb = [nld.Suggestion(
            a_rel="Home/car", b_rel="Home/auto", a_title="Car sale",
            b_title="Auto trade", a_folder="Home", b_folder="Home",
            score=0.82, shared_terms=[], same_folder=True, signal="embedding")]
        report = nld.build_report(notes, tfidf, today="2026-05-30",
                                  embed_suggestions=emb)
        self.assertIn("## Shared-vocabulary links (TF-IDF)", report)
        self.assertIn("## Semantically related (embedding signal", report)
        self.assertIn("semantically related (cosine 0.820)", report)
        self.assertIn("Signals: **TF-IDF**", report)

    def test_report_embed_unavailable_note(self):
        notes = [self._note("Church/baptism", title="Baptism", body="covenant"),
                 self._note("Church/confirmation", title="Confirmation", body="covenant")]
        tfidf = [nld.Suggestion(
            a_rel="Church/baptism", b_rel="Church/confirmation",
            a_title="Baptism", b_title="Confirmation",
            a_folder="Church", b_folder="Church", score=0.5,
            shared_terms=["covenant"], same_folder=True)]
        report = nld.build_report(notes, tfidf, today="2026-05-30",
                                  embed_suggestions=None, embed_unavailable=True)
        self.assertIn("TF-IDF only", report)
        self.assertNotIn("## Semantically related", report)

    def test_report_confirmed_pair_flagged(self):
        # A pair found by BOTH signals shows the "also semantically related" flag
        # in the TF-IDF section and is NOT duplicated in the embedding section.
        notes = [self._note("Church/baptism", title="Baptism", body="covenant"),
                 self._note("Church/confirmation", title="Confirmation", body="covenant")]
        pair = dict(a_rel="Church/baptism", b_rel="Church/confirmation",
                    a_title="Baptism", b_title="Confirmation",
                    a_folder="Church", b_folder="Church", same_folder=True)
        tfidf = [nld.Suggestion(score=0.5, shared_terms=["covenant"], **pair)]
        emb = [nld.Suggestion(score=0.88, shared_terms=[], signal="embedding", **pair)]
        report = nld.build_report(notes, tfidf, today="2026-05-30", embed_suggestions=emb)
        self.assertIn("also semantically related", report)
        self.assertNotIn("## Semantically related (embedding signal", report)


class TestApplyMode(unittest.TestCase):
    def test_read_only_by_default(self):
        with _Vault() as v:
            a = _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance renewed by the sacrament.")
            b = _write(v, "Church/confirmation.md",
                       "Confirmation covenant ordinance conferring the sacrament.")
            before = {p: p.read_bytes() for p in (a, b)}
            buf = io.StringIO()
            with redirect_stdout(buf):
                nld.main(["--vault", str(v), "--min-score", "0.05"])
            for p in (a, b):
                self.assertEqual(p.read_bytes(), before[p])

    def test_apply_writes_marked_related_and_backup(self):
        with _Vault() as v:
            a = _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance renewed by the sacrament.")
            b = _write(v, "Church/confirmation.md",
                       "Confirmation covenant ordinance conferring the sacrament.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(list((v / "_meta").glob("notes-backup-*.tar.gz"))), 1,
                             "no backup written before apply")
            self.assertIn(nld._APPLY_MARKER, a.read_text(encoding="utf-8"))
            self.assertIn("[[confirmation]]", a.read_text(encoding="utf-8"))
            self.assertIn("[[baptism]]", b.read_text(encoding="utf-8"))
            self.assertNotIn("## Related", (v / "Tech/python.md").read_text(encoding="utf-8"))

    def test_apply_idempotent_and_no_double_section(self):
        with _Vault() as v:
            a = _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance renewed by the sacrament.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            for _ in range(3):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            body = a.read_text(encoding="utf-8")
            self.assertEqual(body.count("## Related"), 1, "duplicate Related sections")
            self.assertEqual(body.count("[[confirmation]]"), 1, "duplicate link")

    def test_apply_merges_into_existing_agent_section(self):
        with _Vault() as v:
            (v / "Church").mkdir(parents=True)
            (v / "Church/baptism.md").write_text(
                "Baptism covenant ordinance sacrament.\n\n## Related\n\n"
                f"%% {nld._APPLY_MARKER}, 2026-05-29. Review / edit / remove freely. %%\n"
                "- [[some-old-note]]\n", encoding="utf-8")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            body = (v / "Church/baptism.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("## Related"), 1)
            self.assertIn("[[some-old-note]]", body)
            self.assertIn("[[confirmation]]", body)

    def test_apply_skips_already_linked(self):
        with _Vault() as v:
            a = _write(v, "Church/baptism.md",
                       "Baptism covenant ordinance sacrament. See [[confirmation]].")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            self.assertNotIn("## Related", a.read_text(encoding="utf-8"))

    def test_apply_no_feedback_loop(self):
        # Injected `[[link]]` text must NOT feed back into the relatedness signal:
        # a third unrelated note that merely shares words with the link TEXT must
        # not start matching. Scoring body excludes the agent Related section.
        with _Vault() as v:
            _write(v, "Church/baptism.md",
                   "Baptism covenant ordinance renewed by the sacrament.")
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            # Re-running finds nothing new (idempotent; no feedback-surfaced pairs).
            notes = nld.build_corpus(v)
            _n, sugg = nld.discover(v, min_score=0.05, top=40)
            want = nld.plan_apply(notes, sugg)
            self.assertEqual(sum(len(x) for x in want.values()), 0,
                             f"feedback loop surfaced new links: {dict(want)}")

    def test_split_related_preserves_human_body(self):
        preserved, existing = nld._split_related("My note.\n\nLots of content.\n")
        self.assertEqual(existing, set())
        self.assertEqual(preserved, "My note.\n\nLots of content.")

    def test_apply_does_not_delete_prose_when_marker_in_text(self):
        # Adversarial review: the marker phrase appearing in PROSE (or a human
        # `## Related`) must not trigger the agent-block split — no data loss.
        with _Vault() as v:
            victim = _write(v, "Church/baptism.md",
                'The feature "Suggested by Agent M link-discovery" audits notes.\n\n'
                'covenant ordinance sacrament baptism confirmation priesthood.\n\n'
                '## Related\n\n- [[my-handpicked-essay]]\n')
            _write(v, "Church/confirmation.md",
                   "Confirmation covenant ordinance conferring the sacrament gift "
                   "priesthood baptism.")
            _write(v, "Tech/python.md", "Asyncio coroutines event loop scheduling.")
            buf = io.StringIO()
            with redirect_stdout(buf):
                nld.main(["--vault", str(v), "--apply", "--min-score", "0.05"])
            after = victim.read_text(encoding="utf-8")
            self.assertIn("covenant ordinance sacrament baptism confirmation priesthood.",
                          after, "--apply DELETED an operator prose paragraph")
            self.assertIn("[[my-handpicked-essay]]", after,
                          "--apply clobbered the human-authored Related link")

    def test_split_related_ignores_marker_in_prose(self):
        body = 'I read about the Suggested by Agent M link-discovery tool today.\n'
        preserved, existing = nld._split_related(body)
        self.assertEqual(existing, set())
        self.assertEqual(preserved, body.rstrip("\n"))


class TestUnitHelpers(unittest.TestCase):
    def test_tokenize_drops_stopwords_and_short(self):
        toks = nld._tokenize("The cat sat on a comfortable mat by the window")
        self.assertNotIn("the", toks)
        self.assertNotIn("on", toks)       # 2-char, below min length
        self.assertIn("comfortable", toks)
        self.assertIn("window", toks)

    def test_strip_markup_removes_html_css_hex_urls(self):
        raw = ('<style>.x { font-family: serif; color: #fffaa5; }</style>'
               'Real prose here. <span style="width:100%">visible</span> '
               '#f497d ![[embed.png]] https://example.com/page apricot')
        cleaned = nld._strip_markup(raw)
        self.assertNotIn("font-family", cleaned)
        self.assertNotIn("#fffaa5", cleaned)
        self.assertNotIn("#f497d", cleaned)
        self.assertNotIn("example.com", cleaned)
        self.assertNotIn("embed.png", cleaned)
        self.assertIn("Real prose here", cleaned)
        self.assertIn("visible", cleaned)
        self.assertIn("apricot", cleaned)

    def test_noise_token_filter(self):
        self.assertTrue(nld._is_noise_token("f497d"))      # hex with digit
        self.assertTrue(nld._is_noise_token("image1"))     # enumerated media
        self.assertTrue(nld._is_noise_token("img12"))
        self.assertTrue(nld._is_noise_token("serif"))      # CSS keyword stopword
        self.assertFalse(nld._is_noise_token("decade"))    # all-alpha hex-chars, real word
        self.assertFalse(nld._is_noise_token("facade"))
        self.assertFalse(nld._is_noise_token("covenant"))
        self.assertFalse(nld._is_noise_token("family"))    # NOT a stopword (corpus vocab)

    def test_spanish_stopwords_dropped_but_content_kept(self):
        toks = nld._tokenize("que los antepasados eran para sellar la familia")
        self.assertNotIn("que", toks)
        self.assertNotIn("los", toks)
        self.assertNotIn("eran", toks)
        self.assertNotIn("para", toks)
        self.assertIn("antepasados", toks)
        self.assertIn("sellar", toks)
        self.assertIn("familia", toks)

    def test_html_boilerplate_does_not_connect_notes(self):
        # Two notes whose ONLY overlap is clipped HTML/CSS boilerplate must not
        # be surfaced as related (regression for the live-dogfood finding).
        css = ('<div style="font-family: serif; width: 100%; color: #fffaa5">'
               '</div> <style>.a { color: #f497d; }</style>')
        with _Vault() as v:
            # No shared PROSE — only the clipped CSS overlaps, and it's stripped.
            _write(v, "Home/a.md", css + " Apricot marmalade canning jars pectin.")
            _write(v, "Home/b.md", css + " Bicycle derailleur cassette tuning chain.")
            _write(v, "Home/c.md", css + " Ceramic kiln glaze firing cone earthenware.")
            _notes, sugg = nld.discover(v, min_score=0.18, top=40)
            self.assertEqual(_pairset(sugg), set(),
                             f"HTML boilerplate connected notes: {_pairset(sugg)}")

    def test_resolve_link_variants(self):
        self.assertEqual(nld._resolve_link("foo"), ("foo", None))
        self.assertEqual(nld._resolve_link("foo|alias"), ("foo", None))
        self.assertEqual(nld._resolve_link("dir/foo"), ("foo", "dir/foo"))
        self.assertEqual(nld._resolve_link("foo.md#heading"), ("foo", None))
        self.assertEqual(nld._resolve_link("#anchor-only"), ("", None))

    def test_idf_down_weights_common_terms(self):
        # Direct model check: a term in many notes has lower idf than a rare one.
        with _Vault() as v:
            for i in range(6):
                _write(v, f"Home/n{i}.md", "common common common rare%d unique%d" % (i, i))
            notes = nld.build_corpus(v)
            model = nld.build_model(notes, min_df=1, max_df_ratio=1.0)
            # "common" is in all 6; "rare0" in 1 -> rare has higher idf.
            self.assertIn("common", model.idf)
            if "rare0" in model.idf:
                self.assertGreater(model.idf["rare0"], model.idf["common"])


if __name__ == "__main__":
    unittest.main()
