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
        (self.vault / "personal" / "reference").mkdir(parents=True)
        (self.vault / "personal" / "reference" / "deploy-runbook.md").write_text(
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
        self.assertIn("personal/reference/deploy-runbook.md", paths)

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


if __name__ == "__main__":
    unittest.main()
