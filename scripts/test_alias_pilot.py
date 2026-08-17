#!/usr/bin/env python3
"""Tests for alias_pilot.py — scope, batching, the confirm gate, frontmatter
emission, and the gold-blindness guard as a mechanical property of the code.

`GoldBlindnessTests` is the load-bearing class here. The design's §1 boundary
("an alias engine that reads the answer sheet is disqualified regardless of
its score") is not something a docstring can enforce — these tests instrument
every file this module opens during a real propose run and fail if any of
them, or the prompt built from them, is gold-set-shaped.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alias_backfill as ab  # noqa: E402
import alias_pilot  # noqa: E402


def write(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class ScopeTests(unittest.TestCase):
    def test_project_index_files_are_in_scope(self):
        self.assertTrue(alias_pilot.in_pilot_scope("desk/projects/foo/_index.md"))

    def test_external_tree_is_in_scope(self):
        self.assertTrue(alias_pilot.in_pilot_scope("external/primos/_index.md"))
        self.assertTrue(alias_pilot.in_pilot_scope("external/primos/analysis/_summary.md"))

    def test_plan_archive_files_are_in_scope(self):
        self.assertTrue(alias_pilot.in_pilot_scope(
            "desk/projects/agentm/_harness/archive/x/PLAN.archive.20260724-y.md"))

    def test_ordinary_notes_are_out_of_scope(self):
        for rel in (
            "memory/2026/08/some-note.md",
            "desk/projects/agentm/research/sqlite/reference/bm25-k1-b-constants.md",
            "desk/projects/agentm/_harness/archive/progress-foo.md",
            "desk/projects/agentm/_harness/designs/friday/F1-REAUDIT.md",
        ):
            with self.subTest(rel=rel):
                self.assertFalse(alias_pilot.in_pilot_scope(rel))

    def _fixture_rows(self):
        return [
            {"path": "desk/projects/proj1/_index.md", "flags": [], "status": "active"},
            {"path": "external/extproj/_index.md", "flags": [], "status": "active"},
            {"path": "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
             "flags": [], "status": "active"},
            # out of scope — must never appear in the selection
            {"path": "memory/2026/08/unrelated.md", "flags": [], "status": "active"},
            # in scope by name, but already aliased — must be excluded
            {"path": "desk/projects/proj2/_index.md", "flags": [], "status": "active"},
        ]

    def _fixture_vault(self, tmp):
        vault = Path(tmp)
        write(vault, "desk/projects/proj1/_index.md",
              "---\nkind: index\nstatus: active\n---\n\nProject 1 overview.\n")
        write(vault, "external/extproj/_index.md",
              "---\nkind: index\nstatus: active\n---\n\nExternal project overview.\n")
        write(vault, "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
              "---\nkind: plan\nstatus: active\n---\n\nClosed-out plan narrative.\n")
        write(vault, "memory/2026/08/unrelated.md",
              "---\nkind: preferences\nstatus: active\n---\n\nUnrelated memory note.\n")
        write(vault, "desk/projects/proj2/_index.md",
              "---\nkind: index\nstatus: active\naliases: [already here]\n---\n\nProject 2.\n")
        return vault

    def test_select_scope_excludes_out_of_scope_and_already_aliased_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._fixture_vault(tmp)
            scope = alias_pilot.select_scope(str(vault), self._fixture_rows(), limit=300)
            paths = sorted(c.path for c in scope)
            self.assertEqual(paths, sorted([
                "desk/projects/proj1/_index.md",
                "external/extproj/_index.md",
                "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
            ]))

    def test_select_scope_honors_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._fixture_vault(tmp)
            scope = alias_pilot.select_scope(str(vault), self._fixture_rows(), limit=2)
            self.assertEqual(len(scope), 2)
            # deterministic — the first two in sorted path order
            self.assertEqual([c.path for c in scope], sorted([
                "desk/projects/proj1/_index.md",
                "external/extproj/_index.md",
                "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
            ])[:2])


class ProjectContextTests(unittest.TestCase):
    def test_note_gets_its_own_projects_index_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write(vault, "desk/projects/proj1/_index.md",
                  "---\nkind: index\n---\n\nProject 1 is about widgets.\n")
            write(vault, "desk/projects/proj1/sub/note.md", "---\nkind: x\n---\n\nbody\n")
            ctx = alias_pilot.project_context(str(vault), "desk/projects/proj1/sub/note.md")
            self.assertIn("Project 1 is about widgets.", ctx)

    def test_the_index_itself_gets_no_self_referential_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write(vault, "desk/projects/proj1/_index.md",
                  "---\nkind: index\n---\n\nProject 1 is about widgets.\n")
            ctx = alias_pilot.project_context(str(vault), "desk/projects/proj1/_index.md")
            self.assertEqual(ctx, "")

    def test_external_notes_get_their_own_projects_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write(vault, "external/extproj/_index.md", "---\nkind: index\n---\n\nExternal thing.\n")
            write(vault, "external/extproj/analysis/_summary.md", "---\nkind: x\n---\n\nbody\n")
            ctx = alias_pilot.project_context(str(vault), "external/extproj/analysis/_summary.md")
            self.assertIn("External thing.", ctx)

    def test_no_index_means_no_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write(vault, "memory/2026/08/note.md", "---\nkind: x\n---\n\nbody\n")
            self.assertEqual(alias_pilot.project_context(str(vault), "memory/2026/08/note.md"), "")

    def test_context_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write(vault, "desk/projects/proj1/_index.md",
                  "---\nkind: index\n---\n\n" + ("x" * 2000) + "\n")
            write(vault, "desk/projects/proj1/note.md", "---\nkind: x\n---\n\nbody\n")
            ctx = alias_pilot.project_context(str(vault), "desk/projects/proj1/note.md", max_chars=50)
            self.assertLessEqual(len(ctx), 50)


class PromptTests(unittest.TestCase):
    def test_task_rules_are_reused_from_alias_backfill(self):
        note = ab.Candidate(path="desk/projects/x/_index.md", flags=[], status="active",
                             head="kind: index", body="Project overview.")
        prompt = alias_pilot.build_pilot_prompt([(note, "")], 1100)
        self.assertIn(ab.TASK_RULES, prompt)

    def test_context_section_only_appears_when_context_is_non_empty(self):
        note = ab.Candidate(path="desk/projects/x/sub.md", flags=[], status="active",
                             head="kind: x", body="body text")
        with_ctx = alias_pilot.build_pilot_prompt([(note, "some project context")], 1100)
        without_ctx = alias_pilot.build_pilot_prompt([(note, "")], 1100)
        self.assertIn("project context (background only):\nsome project context", with_ctx)
        self.assertNotIn("project context (background only)", without_ctx)

    def test_every_note_id_is_present(self):
        notes = [
            (ab.Candidate(path=f"desk/projects/x/{i}.md", flags=[], status="active",
                          head="kind: x", body=f"body {i}"), "")
            for i in range(3)
        ]
        prompt = alias_pilot.build_pilot_prompt(notes, 1100)
        for i in range(3):
            self.assertIn(f"--- note id={i}", prompt)

    def test_default_variant_is_content_and_omits_the_structural_addendum(self):
        note = ab.Candidate(path="desk/projects/x/_index.md", flags=[], status="active",
                             head="kind: index", body="Project overview.")
        prompt = alias_pilot.build_pilot_prompt([(note, "")], 1100)
        self.assertNotIn(alias_pilot.STRUCTURAL_ADDENDUM, prompt)

    def test_explicit_content_variant_is_byte_identical_to_the_default(self):
        note = ab.Candidate(path="desk/projects/x/_index.md", flags=[], status="active",
                             head="kind: index", body="Project overview.")
        default_prompt = alias_pilot.build_pilot_prompt([(note, "ctx")], 1100)
        explicit_prompt = alias_pilot.build_pilot_prompt([(note, "ctx")], 1100, variant="content")
        self.assertEqual(default_prompt, explicit_prompt)

    def test_structural_variant_adds_the_role_addendum_without_removing_content_rules(self):
        note = ab.Candidate(path="desk/projects/x/_index.md", flags=[], status="active",
                             head="kind: index", body="Project overview.")
        prompt = alias_pilot.build_pilot_prompt([(note, "")], 1100, variant="structural")
        self.assertIn(alias_pilot.STRUCTURAL_ADDENDUM, prompt)
        self.assertIn(ab.TASK_RULES, prompt)


class ProposeApplyTests(unittest.TestCase):
    """Propose writes only a journal; apply is the separate, explicit write."""

    def _fixture(self, tmp):
        vault = Path(tmp, "vault")
        write(vault, "desk/projects/proj1/_index.md",
              "---\nkind: index\nstatus: active\n---\n\nProject 1 overview.\n")
        write(vault, "external/extproj/_index.md",
              "---\nkind: index\nstatus: active\n---\n\nExternal overview.\n")
        rows = [
            {"path": "desk/projects/proj1/_index.md", "flags": [], "status": "active"},
            {"path": "external/extproj/_index.md", "flags": [], "status": "active"},
        ]
        canned = json.dumps([
            {"id": 0, "aliases": ["alias one a", "alias two a", "alias three a"]},
            {"id": 1, "aliases": ["alias one b", "alias two b", "alias three b"]},
        ])
        return vault, rows, canned

    def test_propose_writes_a_journal_and_touches_no_vault_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            before = {
                p: (vault / p).read_text(encoding="utf-8")
                for p in ("desk/projects/proj1/_index.md", "external/extproj/_index.md")
            }
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = alias_pilot.main([
                    "--vault", str(vault), "propose",
                    "--journal", str(journal), "--limit", "300",
                ])
            self.assertEqual(rc, 0)
            recs = [json.loads(l) for l in journal.read_text().splitlines()]
            self.assertEqual(len(recs), 2)
            self.assertTrue(all(r["outcome"] == "aliased" for r in recs))
            for p in before:
                self.assertEqual((vault / p).read_text(encoding="utf-8"), before[p])

    def test_apply_writes_frontmatter_from_the_journal_without_recalling_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300"])

            out_journal = Path(tmp, "apply.jsonl")
            with mock.patch.object(ab, "call_model",
                                    side_effect=AssertionError("apply must not call the model")), \
                 mock.patch.object(ab, "require_corpus_write_gate", lambda *a, **k: None), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = alias_pilot.main([
                    "--vault", str(vault), "apply",
                    "--journal", str(journal), "--out-journal", str(out_journal),
                ])
            self.assertEqual(rc, 0)

            text = (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8")
            self.assertIn("aliases: [alias one a, alias two a, alias three a]", text)
            out_recs = [json.loads(l) for l in out_journal.read_text().splitlines()]
            self.assertEqual(len(out_recs), 2)
            self.assertTrue(all("sha_before" in r and "sha_after" in r for r in out_recs))

    def test_apply_is_idempotent_against_a_note_already_aliased(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300"])
            out_journal = Path(tmp, "apply.jsonl")
            with mock.patch.object(ab, "require_corpus_write_gate", lambda *a, **k: None), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "apply",
                                   "--journal", str(journal), "--out-journal", str(out_journal)])
                text_after_first = (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8")
                # a second apply of the same propose journal must not double-write
                alias_pilot.main(["--vault", str(vault), "apply",
                                   "--journal", str(journal), "--out-journal", str(out_journal)])
            text_after_second = (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8")
            self.assertEqual(text_after_first, text_after_second)
            self.assertEqual(text_after_second.count("aliases: ["), 1)

    def test_apply_is_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300"])

            out_journal = Path(tmp, "apply.jsonl")
            with mock.patch.object(ab, "require_corpus_write_gate",
                                    side_effect=SystemExit("refused")):
                with self.assertRaises(SystemExit):
                    with contextlib.redirect_stdout(io.StringIO()):
                        alias_pilot.main(["--vault", str(vault), "apply",
                                           "--journal", str(journal),
                                           "--out-journal", str(out_journal)])
            text = (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8")
            self.assertNotIn("aliases:", text)

    def test_allow_ungated_writes_without_asking_the_gate(self):
        """The named opt-out for a frozen-corpus scratch copy, which cannot pass
        the gate at all (it is not a git repository)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300"])

            out_journal = Path(tmp, "apply.jsonl")
            with mock.patch.object(ab, "require_corpus_write_gate",
                                    side_effect=AssertionError("gate must not be consulted")), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = alias_pilot.main([
                    "--vault", str(vault), "apply",
                    "--journal", str(journal), "--out-journal", str(out_journal),
                    "--allow-ungated",
                ])
            self.assertEqual(rc, 0)
            text = (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8")
            self.assertIn("aliases: [", text)

    def test_revert_and_reapply_delegate_to_alias_backfills_own_implementation(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, rows, canned = self._fixture(tmp)
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300"])
            out_journal = Path(tmp, "apply.jsonl")
            with mock.patch.object(ab, "require_corpus_write_gate", lambda *a, **k: None), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "apply",
                                   "--journal", str(journal), "--out-journal", str(out_journal)])

            original = "---\nkind: index\nstatus: active\n---\n\nProject 1 overview.\n"
            with contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "revert", "--journal", str(out_journal)])
            self.assertEqual(
                (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8"), original
            )
            with mock.patch.object(ab, "require_corpus_write_gate", lambda *a, **k: None), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "reapply", "--journal", str(out_journal)])
            self.assertIn(
                "aliases: [",
                (vault / "desk/projects/proj1/_index.md").read_text(encoding="utf-8"),
            )


class GoldBlindnessTests(unittest.TestCase):
    """The mechanical half of the design's §1 boundary.

    Not a claim in a docstring: every file this module opens during a real
    propose run is instrumented, and the prompt it builds is checked against
    the eight gold questions' own literal text.
    """

    _FULL_SOURCE = Path(alias_pilot.__file__).read_text(encoding="utf-8")
    # The module docstring is allowed to *discuss* the boundary by name (that is
    # this file's own documentation of what it excludes); what must never
    # appear is one of these terms in the CODE below it — an import, an open,
    # a literal path the code actually reaches for.
    SOURCE = _FULL_SOURCE.split('"""', 2)[-1]

    # Substrings that would only appear in the code if it named or reached for
    # the gold set or the oracle's scratch artifacts.
    BANNED_CODE_SUBSTRINGS = [
        "gold-set", "gold_set", "GOLD_SET", "GoldSet",
        "alias-oracle", "alias_oracle", "AliasOracle",
        "06-write-aliases",
    ]

    GOLD_QUESTIONS = [
        "Give me a list of my pending project ideas for the house?",
        "Why did AgentM never fully realize the vault vision that I had?",
        "Show me where we kept the notes for primos",
        "Does agentm still support multiple operating systems?",
        "How am I optimizing model cost despite the limitaitons of Claude and "
        "Antigravity in changing models on the fly automtically?",
        "In my developer workflows, tell me what I do and don't do automaticaly and why",
        "In the built-in ranker, can we change how quickly repeated occurrences "
        "of a term stop adding to a document's score, or is the per-field boost "
        "the only lever we get?",
        "Which outside project decided against embeddings the way we did?",
    ]

    def test_source_never_names_the_gold_set_or_the_oracle(self):
        for term in self.BANNED_CODE_SUBSTRINGS:
            self.assertNotIn(term, self.SOURCE)

    def test_no_file_opened_during_a_full_propose_run_is_gold_or_oracle_shaped(self):
        for variant in ("content", "structural"):
            with self.subTest(variant=variant):
                self._assert_propose_run_opens_no_gold_shaped_path(variant)

    def _assert_propose_run_opens_no_gold_shaped_path(self, variant):
        opened: list[str] = []
        real_open = Path.open

        def spy(self, *a, **kw):
            opened.append(str(self))
            return real_open(self, *a, **kw)

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp, "vault")
            write(vault, "desk/projects/proj1/_index.md",
                  "---\nkind: index\nstatus: active\n---\n\nProject 1 overview, widgets.\n")
            write(vault, "external/extproj/_index.md",
                  "---\nkind: index\nstatus: active\n---\n\nExternal overview.\n")
            write(vault, "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
                  "---\nkind: plan\nstatus: active\n---\n\nClosed-out plan narrative.\n")
            rows = [
                {"path": "desk/projects/proj1/_index.md", "flags": [], "status": "active"},
                {"path": "external/extproj/_index.md", "flags": [], "status": "active"},
                {"path": "desk/projects/proj1/_harness/archive/x/PLAN.archive.20260101-demo.md",
                 "flags": [], "status": "active"},
            ]
            canned = json.dumps([
                {"id": i, "aliases": [f"alias one {i}", f"alias two {i}", f"alias three {i}"]}
                for i in range(3)
            ])
            journal = Path(tmp, "propose.jsonl")
            with mock.patch.object(Path, "open", spy), \
                 mock.patch.object(ab, "classify", return_value=rows), \
                 mock.patch.object(ab, "call_model", return_value=canned), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--vault", str(vault), "propose",
                                   "--journal", str(journal), "--limit", "300",
                                   "--variant", variant])

        self.assertTrue(opened, "the spy recorded no file opens at all — test is not exercising I/O")
        for p in opened:
            low = p.lower()
            self.assertNotIn("gold", low, f"opened a gold-shaped path: {p}")
            self.assertNotIn("oracle", low, f"opened an oracle-shaped path: {p}")

    def test_built_prompt_never_contains_a_gold_questions_literal_text(self):
        notes = [
            (ab.Candidate(path="desk/projects/proj1/_index.md", flags=[], status="active",
                          head="kind: index", body="Project 1 overview, widgets and gadgets."),
             "Project 1 context excerpt."),
            (ab.Candidate(path="external/extproj/_index.md", flags=[], status="active",
                          head="kind: index", body="External project overview."), ""),
        ]
        for variant in ("content", "structural"):
            with self.subTest(variant=variant):
                prompt = alias_pilot.build_pilot_prompt(notes, 1100, variant=variant)
                for q in self.GOLD_QUESTIONS:
                    self.assertNotIn(q, prompt)

    def test_structural_addendum_is_pure_category_language(self):
        """The addendum names roles (list, index, summary, ...) and template
        placeholders only — never a gold question's own words."""
        low = alias_pilot.STRUCTURAL_ADDENDUM.lower()
        for q in self.GOLD_QUESTIONS:
            self.assertNotIn(q.lower(), low)
        for term in self.BANNED_CODE_SUBSTRINGS:
            self.assertNotIn(term.lower(), low)

    def test_generation_function_signatures_take_no_gold_shaped_input(self):
        """`build_pilot_prompt` and `_propose_batch` accept only (note, context)
        pairs, a variant enum, and generation args — there is no parameter
        through which a gold question or a gold-set path could be threaded."""
        import inspect
        sig = inspect.signature(alias_pilot.build_pilot_prompt)
        self.assertEqual(list(sig.parameters), ["batch", "body_chars", "variant"])
        sig2 = inspect.signature(alias_pilot._propose_batch)
        self.assertEqual(list(sig2.parameters), ["batch", "args"])


class OutcomeFilterTests(unittest.TestCase):
    """The outcome filter's contract: an alias survives only if querying the
    lexical arm with that alias's own text returns its own note in the top k.

    The searches are faked here — what is under test is the filter's decision
    logic and its journal, not the daemon's ranking, which has its own tests.
    """

    JOURNAL = [
        {"path": "desk/projects/works/_index.md", "outcome": "aliased", "op": "insert",
         "aliases": ["a working alias", "a useless alias"]},
        {"path": "desk/projects/fails/_index.md", "outcome": "aliased", "op": "insert",
         "aliases": ["another useless alias"]},
        {"path": "desk/projects/skipped/_index.md", "outcome": "skip-too-few",
         "reason": "kept 1 of 3"},
    ]

    # Only "a working alias" retrieves its own note; everything else returns
    # some other note, exactly as a lexically uncompetitive alias would.
    FAKE_RESULTS = {
        "a working alias": ["Agent/desk/projects/works/_index.md", "Agent/other/x.md"],
        "a useless alias": ["Agent/other/x.md", "Agent/other/y.md"],
        "another useless alias": ["Agent/other/x.md"],
    }

    def _run_filter(self, tmp, k=5):
        journal = Path(tmp, "propose.jsonl")
        journal.write_text("\n".join(json.dumps(r) for r in self.JOURNAL), encoding="utf-8")
        out = Path(tmp, "filtered.jsonl")
        with mock.patch.object(alias_pilot, "lexical_top_k",
                               side_effect=lambda a, i, q, kk: self.FAKE_RESULTS[q]), \
             contextlib.redirect_stdout(io.StringIO()):
            alias_pilot.main(["--agentmd", "agentmd", "filter",
                              "--journal", str(journal), "--out-journal", str(out),
                              "--index", str(Path(tmp, "candidate.db")),
                              "--k", str(k), "--path-prefix", "Agent/"])
        return [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_an_alias_that_does_not_retrieve_its_own_note_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            recs = {r["path"]: r for r in self._run_filter(tmp)}
        kept = recs["desk/projects/works/_index.md"]
        self.assertEqual(kept["aliases"], ["a working alias"])
        self.assertEqual(kept["dropped"], ["a useless alias"])

    def test_a_note_whose_aliases_all_fail_drops_out_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            recs = {r["path"]: r for r in self._run_filter(tmp)}
        failed = recs["desk/projects/fails/_index.md"]
        self.assertEqual(failed["outcome"], "filtered-out")
        self.assertNotIn("aliases", failed)

    def test_non_aliased_propose_records_are_not_carried_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            recs = {r["path"]: r for r in self._run_filter(tmp)}
        self.assertNotIn("desk/projects/skipped/_index.md", recs)

    def test_the_comparison_respects_the_path_prefix(self):
        """Search results are vault-root-relative, journals memory-root-relative.
        Without the prefix, a working alias would look like a failure — which is
        a silent, total null, the exact shape the two prior pilots produced."""
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp, "propose.jsonl")
            journal.write_text(json.dumps(self.JOURNAL[0]), encoding="utf-8")
            out = Path(tmp, "filtered.jsonl")
            with mock.patch.object(alias_pilot, "lexical_top_k",
                                   side_effect=lambda a, i, q, kk: self.FAKE_RESULTS[q]), \
                 contextlib.redirect_stdout(io.StringIO()):
                alias_pilot.main(["--agentmd", "agentmd", "filter",
                                  "--journal", str(journal), "--out-journal", str(out),
                                  "--index", str(Path(tmp, "candidate.db")),
                                  "--path-prefix", ""])
            recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(recs[0]["outcome"], "filtered-out")

    def test_lexical_top_k_never_asks_for_the_dense_arm(self):
        """The filter's question is purely lexical, so it must not pay for — or
        be confounded by — the dense arm."""
        captured = {}

        class Proc:
            returncode = 0
            stdout = '{"results": []}'
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return Proc()

        with mock.patch.object(alias_pilot.subprocess, "run", fake_run):
            alias_pilot.lexical_top_k("agentmd", "idx.db", "some alias", 5)
        self.assertIn("-no-embedder", captured["cmd"])
        self.assertIn("-mode", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("-mode") + 1], "fusion")


class GenerationTransportTests(unittest.TestCase):
    """`call_model` must not inherit a working directory that has a CLAUDE.md or
    AGENTS.md above it.

    Claude Code auto-loads those from the cwd's parent chain, so an inherited
    repo cwd silently feeds this repo's own instructions into a generation that
    is meant to be blind to them. The HyDE probe hit exactly this; both alias
    pilots predate the fix.
    """

    def _capture_cwd(self):
        captured = {}

        class Proc:
            returncode = 0
            stdout = "[]"
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cwd"] = kw.get("cwd")
            # The directory must still exist while the subprocess would be running.
            captured["exists"] = Path(kw["cwd"]).is_dir() if kw.get("cwd") else False
            return Proc()

        with mock.patch.object(ab.subprocess, "run", fake_run):
            ab.call_model("a prompt", "sonnet", 60)
        return captured

    def test_call_model_runs_from_a_neutral_directory(self):
        captured = self._capture_cwd()
        self.assertIsNotNone(captured["cwd"], "call_model inherited the caller's cwd")
        self.assertTrue(captured["exists"], "the neutral cwd must exist during the call")

    def test_the_neutral_directory_has_no_agent_instructions_above_it(self):
        captured = self._capture_cwd()
        p = Path(captured["cwd"]).resolve()
        for parent in [p, *p.parents]:
            for name in ("CLAUDE.md", "AGENTS.md"):
                self.assertFalse(
                    (parent / name).is_file(),
                    f"{parent / name} would be auto-loaded into a blind generation",
                )

    def test_the_neutral_directory_is_not_the_repository(self):
        captured = self._capture_cwd()
        self.assertNotEqual(Path(captured["cwd"]).resolve(),
                            Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    unittest.main()
