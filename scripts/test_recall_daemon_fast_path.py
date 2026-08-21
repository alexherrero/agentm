#!/usr/bin/env python3
"""Tests for recall.py's daemon fast path.

The bug these pin: `memory-recall-prompt-submit` returned zero entries on every
prompt against the operator's real vault, and had been doing so silently. The
in-process engine needs ~3170ms to walk 2,096 entries and the interactive budget
is 300ms, so the lexical half was discarded on every single prompt (correctly —
see test_recall_stream_admission.py for why a partial walk is thrown away rather
than ranked) and the semantic half never ran. Both halves empty, honestly
reported, permanently useless.

The fix is not a bigger budget. `agentmd`, the Go daemon, answers the same query
off a maintained index in ~20ms including process spawn, so recall asks it first
and keeps the whole existing engine as the fallback.

Four things here are easy to get wrong and each has its own test:

  1. **None and [] are different answers.** None means the daemon could not be
     asked and the caller must fall back; [] means it searched and nothing
     admissible matched. Collapsing them re-creates GH #92's signature failure —
     a broken recall that reports itself as an empty vault.

  2. **Daemon paths are not vault paths.** Since the git-transport cutover the
     daemon indexes the Obsidian root while recall is pointed at the memory root
     beneath it, so the daemon says `Agent/memory/x.md` where every downstream
     consumer expects `personal/x.md`. Getting this wrong does not crash; it
     silently reads nothing and looks like an empty result.

  3. **The daemon knows none of recall's hygiene rules.** It indexes everything
     by design — `_dream-staging/` proposals, `_inbox/`, `_archive/`, superseded
     entries, and the operator's personal folders. Those exclusions are recall's
     policy and have to be re-applied on this side or the swap quietly repeals
     them.

  4. **A prompt is not a query.** Handing the daemon a raw sentence is slower
     than the entire hook budget and worse ranked than its own keywords, because
     BM25 scores every document any common word appears in.

Hermetic: no daemon, no vault, no network. `subprocess.run` is replaced by a
fake that returns a canned payload, which is what lets these run on a machine
that has never installed `agentmd`.

**Expected values are hand-written literals, never recomputed with the
implementation's own helpers.** A test that derives `personal/x.md` by calling
the same path logic under test would pass for any pair of agreeing bugs.

Run: python3 scripts/test_recall_daemon_fast_path.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import recall  # noqa: E402


def _write(path: Path, body: str, *, kind: str = "fix", status: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    front = f"---\nname: {path.stem}\nkind: {kind}\ntags: []\n"
    if status:
        front += f"status: {status}\n"
    path.write_text(f"{front}---\n\n{body}\n", encoding="utf-8")
    return path


def _payload(*paths: str, matched: int | None = None, note: str = "") -> str:
    """A daemon `search -json` response naming `paths`, best-scored first.

    `note`, when given, mirrors search.go's `SearchOutcome.Note` — omitted
    from the JSON when empty (`json:"note,omitempty"`), the same as the real
    daemon, so a test that does not care about it exercises the exact shape
    every pre-task-5 test already did.
    """
    doc = {
        "results": [
            {"path": p, "score": 20.0 - i, "raw_score": 20.0 - i,
             "captured": "2026-08-10T04:13:43Z", "captured_source": "mtime",
             "snippet": "…"}
            for i, p in enumerate(paths)
        ],
        "matched": len(paths) if matched is None else matched,
    }
    if note:
        doc["note"] = note
    return json.dumps(doc)


class _FakeDaemon:
    """Stands in for `subprocess.run`, recording the argv it was handed."""

    def __init__(self, *, stdout: str = "", returncode: int = 0,
                 stderr: str = "", raises: BaseException | None = None):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    @property
    def query_sent(self) -> str:
        """The search string the daemon was asked for (argv's last element)."""
        return self.calls[0][-1]


class _VaultFixture(unittest.TestCase):
    """A vault at `<root>/Agent`, with the operator's own folders alongside it.

    Mirrors the real post-cutover layout: the daemon indexes `<root>` and names
    everything `Agent/...` or `Church/...`, while recall is pointed at
    `<root>/Agent` and speaks in paths relative to that.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "Vault"
        self.vault = self.root / "Agent"
        _write(self.vault / "memory" / "zorbulax.md", "The zorbulax subsystem.")
        _write(self.vault / "memory" / "retired.md", "Old news.", status="superseded")
        _write(self.vault / "memory" / "_inbox" / "unfiled.md", "Unfiled zorbulax.")
        _write(self.vault / "memory" / "_archive" / "old.md", "Archived zorbulax.")
        _write(self.vault / "desk/scratch" / "batch" / "prop.md", "Proposal copy.")
        _write(self.root / "Church" / "talk.md", "A talk about patterns.")

    def _run(self, fake, **kw):
        with unittest.mock.patch.object(recall.subprocess, "run", fake):
            return recall._daemon_search(
                vault=self.vault, query_text="zorbulax subsystem",
                status=kw.pop("status", {}), **kw
            )


class QueryExtractionTests(unittest.TestCase):
    """Rule 4: the daemon is asked with content words, not the prompt."""

    def test_stopwords_are_dropped_and_order_is_kept(self):
        self.assertEqual(
            recall._daemon_query_terms(
                "what did we decide about the vault git transport?"
            ),
            "decide vault git transport",
        )

    def test_repeats_and_short_tokens_are_dropped(self):
        self.assertEqual(
            recall._daemon_query_terms("go to the CI gate, the CI gate is red"),
            "gate red",
        )

    def test_the_term_count_is_capped(self):
        """A pathological prompt cannot hand the daemon an unbounded query."""
        terms = recall._daemon_query_terms(
            "alpha bravo charlie delta echo foxtrot golf hotel india", cap=6
        )
        self.assertEqual(terms, "alpha bravo charlie delta echo foxtrot")

    def test_a_prompt_of_pure_stopwords_yields_nothing(self):
        self.assertEqual(recall._daemon_query_terms("what is it about?"), "")

    def test_a_contentless_prompt_does_not_reach_the_daemon(self):
        """Better to fall back than to search for the empty string."""
        fake = _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
        status: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(recall.subprocess, "run", fake):
                out = recall._daemon_search(
                    vault=Path(tmp), query_text="what is it about?", status=status
                )
        self.assertIsNone(out)
        self.assertEqual(fake.calls, [], "the daemon was spawned for no terms")
        self.assertIn("content words", status["reason"])

    def test_the_extracted_terms_are_what_gets_sent(self):
        fake = _FakeDaemon(stdout=_payload())
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(recall.subprocess, "run", fake):
                recall._daemon_search(
                    vault=Path(tmp),
                    query_text="why is the recall hook returning zero entries",
                    status={},
                )
        self.assertEqual(fake.query_sent, "recall hook returning zero entries")


class ModeAndQuestionWiringTests(unittest.TestCase):
    """Hook cutover (task 5): the daemon call asks for `-mode hybrid
    -question <prompt>` — the `+question` arm the ladder measured at 75.0%,
    not the `-mode` flag's `and` default every prior test in this file ran
    under.
    """

    def _sent(self, prompt: str, **kw) -> list[str]:
        fake = _FakeDaemon(stdout=_payload())
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(recall.subprocess, "run", fake):
                recall._daemon_search(vault=Path(tmp), query_text=prompt, status={}, **kw)
        return fake.calls[0]

    def test_mode_hybrid_is_always_requested(self):
        argv = self._sent("what did we decide about the vault git transport?")
        self.assertIn("-mode", argv)
        self.assertEqual(argv[argv.index("-mode") + 1], "hybrid")

    def test_the_question_flag_carries_the_whole_raw_prompt(self):
        """Not the reduced terms — task 3.5's mechanism only works if the

        dense arm sees the natural question, and the production hook's only
        asset over the CLI is that it already holds one.
        """
        prompt = "what did we decide about the vault git transport?"
        argv = self._sent(prompt)
        self.assertIn("-question", argv)
        self.assertEqual(argv[argv.index("-question") + 1], prompt)

    def test_the_positional_query_stays_the_reduced_terms_not_the_question(self):
        """The lexical arms must never see the raw prompt (rule 4) — -question

        is additive, it does not replace what the bare positional carries.
        """
        argv = self._sent("what did we decide about the vault git transport?")
        self.assertEqual(argv[-1], "decide vault git transport")

    def test_lex3_is_never_requested(self):
        """Refuted (task 4) and stays default-off; nothing in this rung may

        opt the hook into it.
        """
        argv = self._sent("what did we decide about the vault git transport?")
        self.assertNotIn("-lex3", argv)

    def test_a_bare_hook_call_never_passes_vault_or_index_overrides(self):
        """daemon_vault/daemon_index are a measurement-only escape hatch

        (mirrors retrieval_scorecard.py's --vault/--index) — the production
        hook must always let the daemon resolve its own live-config vault.
        """
        argv = self._sent("what did we decide about the vault git transport?")
        self.assertNotIn("-vault", argv)
        self.assertNotIn("-index", argv)

    def test_the_vault_and_index_overrides_pass_through_when_both_given(self):
        argv = self._sent(
            "what did we decide about the vault git transport?",
            daemon_vault="/corpus/Vault", daemon_index="/corpus/idx.db",
        )
        self.assertIn("-vault", argv)
        self.assertEqual(argv[argv.index("-vault") + 1], "/corpus/Vault")
        self.assertIn("-index", argv)
        self.assertEqual(argv[argv.index("-index") + 1], "/corpus/idx.db")

    def test_an_override_given_alone_is_dropped_rather_than_sent_half_formed(self):
        """A daemon `-vault` with no `-index` (or vice versa) writes the

        snapshot's index over the operator's live one (task 1's own finding) —
        _daemon_search must never send one without the other.
        """
        argv = self._sent(
            "what did we decide about the vault git transport?",
            daemon_vault="/corpus/Vault",
        )
        self.assertNotIn("-vault", argv)
        self.assertNotIn("-index", argv)


class PathTranslationTests(_VaultFixture):
    """Rule 2: daemon paths are re-expressed relative to the memory root."""

    def test_the_memory_root_prefix_is_stripped(self):
        out = self._run(_FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md")))
        self.assertEqual([r["path"] for r in out], ["memory/zorbulax.md"])
        self.assertEqual(out[0]["slug"], "zorbulax")

    def test_the_translated_path_resolves_under_the_vault(self):
        """The real assertion behind the rename: the file can then be read."""
        out = self._run(_FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md")))
        self.assertTrue((self.vault / out[0]["path"]).is_file())

    def test_a_daemon_rooted_at_the_memory_root_needs_no_stripping(self):
        """`memory_root` unset — the install that never moved."""
        with unittest.mock.patch.object(
            recall.subprocess, "run", _FakeDaemon(stdout=_payload("memory/zorbulax.md"))
        ):
            out = recall._daemon_search(
                vault=self.vault, query_text="zorbulax", status={}
            )
        self.assertEqual([r["path"] for r in out], ["memory/zorbulax.md"])

    def test_a_path_that_resolves_nowhere_is_dropped_not_guessed(self):
        out = self._run(_FakeDaemon(stdout=_payload("Agent/memory/ghost.md")))
        self.assertEqual(out, [])


class ScopeTests(_VaultFixture):
    """The operator's own folders are indexed but not ordinarily recalled."""

    def test_notes_outside_the_memory_root_are_admitted_by_default(self):
        """The default flipped, and the flip is the point.

        This used to assert the opposite: a note outside the memory root was
        dropped. That boundary was drawn after a real leak — 13% of top-5 results
        across 20 prompts fell outside `Agent/`, and "what should I work on next"
        returned two Church notes — but it cured the leak by amputation, and an
        invisible space is how this vault lost 9,786 notes once already.

        The cure is now rank dampening in the daemon rather than a filter in
        recall: a strong distinctive match clears the multiplier and a weak
        semantic neighbour does not. So recall admits everything and marks what
        came from outside, which is what lets a personal note answer a question
        only it can answer.
        """
        out = self._run(_FakeDaemon(
            stdout=_payload("Church/talk.md", "Agent/memory/zorbulax.md")
        ))
        self.assertEqual(len(out), 2, "a note outside the memory root was dropped")
        self.assertTrue(out[0]["external"],
                        "an admitted outside hit must be marked as external")

    def test_the_memory_root_boundary_is_still_reachable(self):
        """The way back. A default is a decision, not a one-way door, and this
        one reverses a boundary that was drawn for a measured reason."""
        out = self._run(
            _FakeDaemon(stdout=_payload("Church/talk.md", "Agent/memory/zorbulax.md")),
            scope="memory-root",
        )
        self.assertEqual([r["path"] for r in out], ["memory/zorbulax.md"])

    def test_scope_vault_admits_them_and_marks_them_external(self):
        out = self._run(
            _FakeDaemon(stdout=_payload("Church/talk.md", "Agent/memory/zorbulax.md")),
            scope="vault",
        )
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["external"])
        self.assertTrue((self.vault / out[0]["path"]).is_file(),
                        "an external hit must still be readable")
        self.assertNotIn("external", out[1])

    def test_the_env_var_selects_the_scope(self):
        with unittest.mock.patch.dict(
            recall.os.environ, {recall.DAEMON_SCOPE_ENV: "vault"}
        ):
            out = self._run(_FakeDaemon(stdout=_payload("Church/talk.md")))
        self.assertEqual(len(out), 1)


class HygieneTests(_VaultFixture):
    """Rule 3: recall's exclusions survive the engine swap."""

    def test_dream_staging_inbox_and_archive_are_all_excluded(self):
        out = self._run(_FakeDaemon(stdout=_payload(
            "Agent/desk/scratch/batch/prop.md",
            "Agent/memory/_inbox/unfiled.md",
            "Agent/memory/_archive/old.md",
            "Agent/memory/zorbulax.md",
        )))
        self.assertEqual([r["path"] for r in out], ["memory/zorbulax.md"])

    def test_inbox_and_archive_reopen_on_request(self):
        out = self._run(
            _FakeDaemon(stdout=_payload(
                "Agent/memory/_inbox/unfiled.md",
                "Agent/memory/_archive/old.md",
            )),
            include_inbox=True, include_archive=True,
        )
        self.assertEqual(
            [r["path"] for r in out],
            ["memory/_inbox/unfiled.md", "memory/_archive/old.md"],
        )

    def test_dream_staging_stays_excluded_even_then(self):
        """It is staging content, surfaced to the agent under no flag."""
        out = self._run(
            _FakeDaemon(stdout=_payload("Agent/desk/scratch/batch/prop.md")),
            include_inbox=True, include_archive=True,
        )
        self.assertEqual(out, [])

    def test_always_load_entries_are_deduped_away(self):
        out = self._run(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md")),
            dedup_paths={"memory/zorbulax.md"},
        )
        self.assertEqual(out, [])


class FallbackSignalTests(_VaultFixture):
    """Rule 1: None means "ask someone else", [] means "nothing matched"."""

    def test_a_missing_binary_returns_none(self):
        status: dict = {}
        out = self._run(_FakeDaemon(raises=FileNotFoundError()), status=status)
        self.assertIsNone(out)
        self.assertIn("not on PATH", status["reason"])

    def test_a_timeout_returns_none(self):
        status: dict = {}
        out = self._run(
            _FakeDaemon(raises=subprocess.TimeoutExpired(cmd="agentmd", timeout=0.15)),
            status=status,
        )
        self.assertIsNone(out)
        self.assertIn("did not answer", status["reason"])

    def test_a_nonzero_exit_returns_none(self):
        status: dict = {}
        out = self._run(
            _FakeDaemon(stdout="", returncode=1, stderr="index locked"), status=status
        )
        self.assertIsNone(out)
        self.assertIn("index locked", status["reason"])

    def test_unparseable_output_returns_none(self):
        out = self._run(_FakeDaemon(stdout="not json at all"))
        self.assertIsNone(out)

    def test_a_genuine_miss_returns_an_empty_list_not_none(self):
        """The distinction GH #92 was made of. [] is an answer; None is not."""
        status: dict = {}
        out = self._run(_FakeDaemon(stdout=_payload()), status=status)
        self.assertEqual(out, [])
        self.assertIsNotNone(out)
        self.assertTrue(status["ran"])

    def test_everything_filtered_out_still_counts_as_searched(self):
        """A full filter is a result, not a failure.

        The fixture used to be `Church/talk.md`, filtered by the memory-root
        scope. That boundary is gone — the daemon dampens an outside space rather
        than recall hiding it — so the note is admitted now and the test would be
        asserting nothing. `_inbox/` is one of the hygiene filters recall still
        applies on its own side, which is the same shape of full filter the test
        was always about."""
        status: dict = {}
        out = self._run(
            _FakeDaemon(stdout=_payload("Agent/memory/_inbox/mined-fragment.md")),
            status=status)
        self.assertEqual(out, [], "an _inbox note reached the caller")
        self.assertTrue(status["ran"], "a full filter is a result, not a failure")


class PromptSubmitIntegrationTests(_VaultFixture):
    """The wiring: daemon first, existing engine untouched behind it."""

    def _submit(self, fake, prompt="zorbulax subsystem"):
        import io
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(recall.subprocess, "run", fake):
            rc = recall.prompt_submit(
                vault=self.vault, prompt=prompt, stdout=out, stderr=err
            )
        return rc, out.getvalue(), err.getvalue()

    def test_the_daemon_answer_is_used_and_the_walk_never_runs(self):
        spy = unittest.mock.Mock(side_effect=AssertionError("query() was called"))
        with unittest.mock.patch.object(recall, "query", spy):
            rc, out, err = self._submit(
                _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
            )
        self.assertEqual(rc, 0)
        self.assertIn("zorbulax", out)
        self.assertIn("engine: daemon", err)
        self.assertIn("Loaded 1 relevant", err)

    def test_the_searched_terms_are_reported_not_the_prompt(self):
        _, _, err = self._submit(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md")),
            prompt="what did we decide about zorbulax?",
        )
        self.assertIn("terms: 'decide zorbulax'", err)

    def test_a_declining_daemon_falls_back_to_the_existing_engine(self):
        spy = unittest.mock.Mock(return_value=[])
        with unittest.mock.patch.object(recall, "query", spy):
            _, _, err = self._submit(_FakeDaemon(raises=FileNotFoundError()))
        self.assertEqual(spy.call_count, 1, "the in-process engine must still run")
        self.assertIn("daemon declined", err)
        self.assertIn("not on PATH", err)

    def test_a_superseded_entry_cannot_return_through_the_faster_engine(self):
        _, out, err = self._submit(_FakeDaemon(stdout=_payload(
            "Agent/memory/retired.md", "Agent/memory/zorbulax.md"
        )))
        self.assertNotIn("Old news", out)
        self.assertIn("Loaded 1 relevant", err)

    def test_a_daemon_hit_reports_its_score_and_never_a_zero_sim(self):
        """`sim=0.00` would read as a semantic match that failed.

        `daemon-hybrid`, not `daemon-lexical`, since the hook cutover (task 5):
        the daemon call now asks for `-mode hybrid` by default, and this
        fixture's payload carries no degrade note, so the dense arm is taken
        to have actually contributed. See
        test_a_degraded_hybrid_hit_is_labelled_lexical_not_hybrid below for the
        case where it did not.
        """
        _, out, _ = self._submit(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
        )
        self.assertIn("score=20.00 daemon-hybrid", out)
        self.assertNotIn("sim=", out)

    def test_heat_is_recorded_for_memory_entries_but_not_external_ones(self):
        """A Church talk is readable context, not curated memory.

        Recording a hit on one would age the operator's own tree on the memory
        engine's decay clock — bookkeeping about a corpus it does not own.
        """
        import heat_policy

        seen: list[str] = []
        with unittest.mock.patch.dict(
            recall.os.environ, {recall.DAEMON_SCOPE_ENV: "vault"}
        ), unittest.mock.patch.object(
            heat_policy, "record_hit", lambda vault, slug: seen.append(slug)
        ):
            _, _, err = self._submit(_FakeDaemon(stdout=_payload(
                "Church/talk.md", "Agent/memory/zorbulax.md"
            )))
        self.assertIn("Loaded 2 relevant", err)
        self.assertEqual(seen, ["zorbulax"])


class SpaceLabelTests(unittest.TestCase):
    """`_hit_space` — which of the daemon's indexed spaces a hit falls under.

    Pure function, no daemon or vault needed: the injection policy (task 5)
    asks for this on every daemon-sourced hit regardless of which arm found
    it, so the mapping itself is tested in isolation from the wiring that
    calls it.
    """

    def test_memory_desk_and_external_are_recognized(self):
        self.assertEqual(recall._hit_space("memory/zorbulax.md"), "memory")
        self.assertEqual(recall._hit_space("desk/projects/agentm/roadmap.md"), "desk")
        self.assertEqual(recall._hit_space("external/some-note.md"), "external")

    def test_a_space_outside_the_landed_scope_is_reported_honestly(self):
        """The lexical arms range over the whole indexed tree, not just the

        three the vector arm covers — an unmatched hit gets its real top-level
        directory, never a forced (and wrong) label.
        """
        self.assertEqual(recall._hit_space("_meta/health/goldv2/NOTES.md"), "_meta")

    def test_an_empty_path_does_not_raise(self):
        self.assertEqual(recall._hit_space(""), "?")


class HookCutoverInjectionTests(_VaultFixture):
    """The metadata task 5's injection policy asks for actually reaches the
    agent's context: mode-aware labeling, space, and the whole-injection
    disclaimer — inject with metadata, never a manufactured empty.
    """

    def _submit(self, fake, prompt="zorbulax subsystem"):
        import io
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(recall.subprocess, "run", fake):
            rc = recall.prompt_submit(
                vault=self.vault, prompt=prompt, stdout=out, stderr=err
            )
        return rc, out.getvalue(), err.getvalue()

    def test_a_desk_hit_is_labelled_with_its_own_space(self):
        _write(self.vault / "desk" / "projects" / "roadmap.md", "The roadmap.")
        _, out, _ = self._submit(_FakeDaemon(stdout=_payload(
            "Agent/desk/projects/roadmap.md"
        )))
        self.assertIn("space: desk", out)

    def test_a_memory_hit_is_labelled_with_its_own_space(self):
        _, out, _ = self._submit(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
        )
        self.assertIn("space: memory", out)

    def test_a_degraded_hybrid_hit_is_labelled_lexical_not_hybrid(self):
        """The daemon's own words (search.go's searchHybrid) for "the dense

        arm was asked for and had nothing to embed with" must be believed
        over the mode that was requested — claiming daemon-hybrid here would
        be exactly the silent-downgrade task 5's rule exists to catch.
        """
        _, out, err = self._submit(_FakeDaemon(stdout=_payload(
            "Agent/memory/zorbulax.md",
            note="hybrid was requested but no query vector was available; "
                 "this is the lexical arm alone (check `agentmd status` for the embedder)",
        )))
        self.assertIn("score=20.00 daemon-lexical", out)
        self.assertNotIn("daemon-hybrid", out)
        self.assertIn("mode=lexical", err)

    def test_an_un_degraded_hybrid_run_reports_mode_hybrid_in_the_transparency_line(self):
        _, _, err = self._submit(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
        )
        self.assertIn("mode=hybrid", err)

    def test_the_daemons_note_is_surfaced_on_the_transparency_line(self):
        note = "hybrid was requested but no query vector was available; this is the lexical arm alone"
        _, _, err = self._submit(_FakeDaemon(stdout=_payload(
            "Agent/memory/zorbulax.md", note=note
        )))
        self.assertIn(note, err)

    def test_daemon_sourced_results_carry_the_candidates_not_answers_disclaimer(self):
        _, out, _ = self._submit(
            _FakeDaemon(stdout=_payload("Agent/memory/zorbulax.md"))
        )
        self.assertIn("not verified answers", out)

    def test_the_in_process_fallback_carries_no_daemon_disclaimer(self):
        """The disclaimer names the daemon's own similarity-based candidates —

        it must not print when the in-process engine (a different ranking,
        this task does not touch) is what actually answered.
        """
        spy = unittest.mock.Mock(return_value=[])
        with unittest.mock.patch.object(recall, "query", spy):
            _, out, _ = self._submit(_FakeDaemon(raises=FileNotFoundError()))
        self.assertNotIn("not verified answers", out)

    def test_an_honest_empty_from_the_daemon_still_prints_nothing_extra(self):
        """No floor, no manufactured rejection: a lexical arm that genuinely

        found nothing must pass through as nothing, exactly as it did before
        this task — the ground rule the plan's own injection-policy amendment
        names explicitly.
        """
        rc, out, err = self._submit(_FakeDaemon(stdout=_payload()))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("Loaded 0 relevant", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
