#!/usr/bin/env python3
"""Recall query CLI's time budget must survive a cold (unwarmed) embed call.

Hardening II cold-install sweep found that `recall.py query` silently
returned zero results against a genuinely fresh vault: the CLI defaulted
its --budget-ms to the interactive UserPromptSubmit hook's tight 300ms
budget, which a cold sentence-transformers load routinely exceeds — the
deadline elapsed before the search ever produced a result, and the CLI
printed nothing (exit 0, not an error). These tests pin the fix: the CLI's
own default is a separate, larger constant, and a slow-but-real embed call
that would have starved the old default still returns a result under it.

Run directly:
    cd scripts && python3 -m unittest test_recall_query_cli_budget
"""
from __future__ import annotations

import json
import os
import subprocess
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


class TestQueryCliBudgetDefault(unittest.TestCase):
    def test_query_subcommand_default_is_not_the_tight_interactive_budget(self):
        args = recall._parse_args(["query", "some text"])
        self.assertEqual(args.budget_ms, recall.QUERY_CLI_BUDGET_MS)
        self.assertGreater(recall.QUERY_CLI_BUDGET_MS, recall.PROMPT_SUBMIT_BUDGET_MS)

    def test_interactive_hook_budgets_are_unchanged(self):
        # SessionStart/UserPromptSubmit are a separate, locked design call
        # (plan #7a part 2) — this fix must not touch either.
        self.assertEqual(recall.SESSION_START_BUDGET_MS, 500)
        self.assertEqual(recall.PROMPT_SUBMIT_BUDGET_MS, 300)


class TestQueryCliBudgetSurvivesSlowEmbed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "memory" / "reference").mkdir(parents=True)
        (self.vault / "memory" / "reference" / "deploy-runbook.md").write_text(
            "the deployment runbook staging gate lives at ops/deploy.md",
            encoding="utf-8",
        )
        self._real_embed_text = embed.embed_text

    def tearDown(self):
        self._tmp.cleanup()

    def _slow_embed_text(self, text, *, mode=None):
        # Long enough to exceed the old 300ms interactive-hook default;
        # nowhere close to the new 10s CLI default. Deterministic, no real
        # model load — the point is the deadline arithmetic, not embedding
        # quality.
        time.sleep(0.5)
        return self._real_embed_text(text, mode="stub")

    def test_generous_budget_returns_a_result_despite_a_slow_embed_call(self):
        with unittest.mock.patch.object(embed, "embed_text", side_effect=self._slow_embed_text):
            deadline = time.monotonic() + (recall.QUERY_CLI_BUDGET_MS / 1000.0)
            results = recall.query(
                vault=self.vault,
                query_text="deployment runbook staging gate",
                k=5,
                deadline=deadline,
            )
        paths = [r["path"] for r in results]
        self.assertIn("memory/reference/deploy-runbook.md", paths)

    def test_old_tight_budget_would_have_starved_the_same_slow_embed_call(self):
        # Documents the mechanism the fix addresses: reproduces the bug
        # under the interactive hook's own budget so a future change can't
        # silently widen QUERY_CLI_BUDGET_MS back down without this test
        # explaining why that budget specifically is too tight here.
        with unittest.mock.patch.object(embed, "embed_text", side_effect=self._slow_embed_text):
            deadline = time.monotonic() + (recall.PROMPT_SUBMIT_BUDGET_MS / 1000.0)
            results = recall.query(
                vault=self.vault,
                query_text="deployment runbook staging gate",
                k=5,
                deadline=deadline,
            )
        # BM25 still runs after the vec search bails on the elapsed
        # deadline (its own per-file deadline check tolerates the overrun
        # window), so this doesn't reliably reproduce an *empty* result --
        # what it pins is the vec half never contributing under this
        # budget: sim stays 0.0 for every hit.
        self.assertTrue(all(r["sim"] == 0.0 for r in results))


class TestInteractiveBudgetEnvOverride(unittest.TestCase):
    """RECALL_BUDGET_MS widens the two hook-driven budgets.

    The shell/pwsh hook wrappers pass no flags, so the environment is the only
    channel a caller has to ask for a budget other than the production
    constant. The caller that needs one is a test driving the hook end-to-end:
    recall pays a fixed setup cost before the corpus walk starts, and on a
    loaded CI runner that alone can exhaust 300ms, at which point the lexical
    stream is discarded and the hook prints nothing on a two-entry vault.
    """

    def test_hook_subcommands_defer_their_budget_to_the_resolver(self):
        # A literal argparse default here would shadow the env silently --
        # None is what lets it be consulted at all.
        self.assertIsNone(recall._parse_args(["session-start"]).budget_ms)
        self.assertIsNone(recall._parse_args(["prompt-submit"]).budget_ms)

    def test_query_is_deliberately_outside_the_env_override(self):
        # query is invoked directly and can already pass --budget-ms.
        self.assertEqual(
            recall._parse_args(["query", "x"]).budget_ms, recall.QUERY_CLI_BUDGET_MS
        )

    def test_an_explicit_flag_beats_the_env(self):
        with unittest.mock.patch.dict(os.environ, {"RECALL_BUDGET_MS": "4321"}):
            self.assertEqual(recall._resolve_budget_ms(None, 300), 4321)
            self.assertEqual(recall._resolve_budget_ms(900, 300), 900)

    def test_an_explicit_zero_survives_the_env(self):
        # 0/negative is the forced-overrun path the smoke tests drive. A
        # truthiness check instead of `is not None` would silently promote it
        # to the env value and delete that path's only trigger.
        with unittest.mock.patch.dict(os.environ, {"RECALL_BUDGET_MS": "4321"}):
            self.assertEqual(recall._resolve_budget_ms(0, 300), 0)
            self.assertEqual(recall._resolve_budget_ms(-1, 300), -1)

    def test_the_default_stands_when_the_env_is_absent_or_junk(self):
        with unittest.mock.patch.dict(os.environ, {}):
            os.environ.pop("RECALL_BUDGET_MS", None)
            self.assertEqual(recall._resolve_budget_ms(None, 300), 300)
        for junk in ("", "   ", "soon", "3.5"):
            with self.subTest(junk=junk):
                with unittest.mock.patch.dict(os.environ, {"RECALL_BUDGET_MS": junk}):
                    self.assertEqual(recall._resolve_budget_ms(None, 300), 300)


class TestInteractiveBudgetEnvReachesTheEngine(unittest.TestCase):
    """The env var must change what the CLI actually emits, not just parse.

    A resolver that agrees with itself proves nothing; this drives recall.py as
    a subprocess the way the hook does. Both literals are observed behavior on
    this fixture, not recomputed from the engine: at 1ms prompt-submit prints
    nothing at all (the corpus walk is discarded before it reaches entry 0, so
    there are no blocks to print), and at 3000ms it prints the entry.
    """

    _SENTINEL = "ENV_BUDGET_SENTINEL_BODY"
    _RECALL_PY = _SKILL_SCRIPTS / "recall.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        (self.vault / "memory").mkdir(parents=True)
        (self.vault / "memory" / "zorbulax-note.md").write_text(
            "---\nname: zorbulax-note\nkind: convention\n"
            "description: a fixture entry\n---\n\n"
            f"{self._SENTINEL} the zorbulax protocol governs widget alignment.\n",
            encoding="utf-8",
        )
        self.home = root / "home"
        self.home.mkdir()
        self.history = root / "recall-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, budget_ms: str):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "MEMORY_VAULT_PATH": str(self.vault),
            "AGENTM_RECALL_HISTORY": str(self.history),
            "RECALL_BUDGET_MS": budget_ms,
        }
        env.pop("AGENTM_INSTALL_PREFIX", None)
        return subprocess.run(
            [sys.executable, str(self._RECALL_PY), "prompt-submit"],
            input=json.dumps({"hookEventName": "UserPromptSubmit",
                              "prompt": "how does the zorbulax protocol align widgets?"}),
            env=env, capture_output=True, text=True,
        )

    def test_a_starved_budget_emits_nothing(self):
        r = self._run("1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        # The engine must say WHY it found nothing -- an empty vault and an
        # unsearched one are not the same event (GH #92).
        self.assertIn("lexical", r.stderr)

    def test_a_widened_budget_injects_the_entry(self):
        r = self._run("3000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self._SENTINEL, r.stdout)
        self.assertIn("Loaded 1 relevant entries: zorbulax-note", r.stderr)


if __name__ == "__main__":
    unittest.main()
