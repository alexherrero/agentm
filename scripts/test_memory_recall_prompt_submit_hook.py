#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-recall-prompt-submit (GH #92).

Twin of test_memory_recall_hook.py, for the UserPromptSubmit half of the
two-hook recall pattern. Drives the bash hook as a subprocess with a synthetic
UserPromptSubmit event JSON on stdin + a fixture vault, proving it ACTUALLY
FIRES (the V4 #39 class of bug — a hook that lands but silently no-ops): it
must inject the entries the prompt matches, dedup them against the always-load
set the SessionStart hook already loaded, stay silent when there is no vault,
and NEVER block the prompt.

"Never blocks" is the whole contract here, and it is exit-code-shaped: a
UserPromptSubmit hook that exits non-zero is what blocking looks like to the
host. So every failure mode below asserts exit 0 alongside whatever else it
checks.

The event differs from the sibling's. recall.py's prompt-submit path reads
exactly one field off the payload — `prompt` (`_read_prompt_from_stdin`) —
where SessionStart reads session_id / cwd / transcript_path / source. Every
other field is ignored, so a payload with no `prompt` is the graceful-skip
trigger here, not a payload with no session_id.

Both state modes are covered via the vault axis: vault-present (recall emits)
vs no-vault / repo-local (recall silent, exit 0).

Time budget: this hook sits on the interactive path and declares a hard
budget. The tests read that number out of the source rather than hard-coding
it, and the behavioral one correlates the transparency line's overrun WARNING
against the measured wall clock — so it holds whether or not a given machine
actually overruns.

Hermetic: a fake `HOME` whose `.claude/.agentm-config.json` resolves the memory
scripts to THIS repo via `source_clones.agentm` (the same config bridge the
sibling uses, and the reason CI — which has neither `~/.claude` nor a
`~/Antigravity/agentm` clone — can exercise the fires-case at all). The fixture
vault is selected via MEMORY_VAULT_PATH (env wins in the resolver), and
`$AGENTM_RECALL_HISTORY` redirects the recall ledger into the fixture, per
recall_counter.default_history_path()'s own warning about tests writing the
operator's real ledger.

What these tests do NOT cover, so nobody reads green here as "recall works in
production": the fake HOME also drops the user site-packages, so `embed.py`'s
default local mode finds no `sentence-transformers` and the vec half of the
query soft-fails in milliseconds — the same state CI runs in. The fixture vault
is small enough that the lexical half completes well inside the budget, so the
injection assertions below are real; on a production-sized vault that walk does
not finish, and per-prompt recall correctly declines to return an arbitrary
partial ranking. These tests say nothing about that case.

That last claim was true of the *walk* and false of everything preceding it,
which is what made the injection assertions flaky. A recall pays a fixed cost
before the walk starts, and
that cost does not shrink with the corpus, so a two-entry fixture vault does not
escape it. On a loaded runner it can consume the entire budget on its own, at
which point the walk is discarded before entry 0 and the hook prints nothing.
The behavioral tests therefore run under an explicit `_TEST_BUDGET_MS` rather
than the production constant; the two that are genuinely *about* the budget pin
it back to `_BUDGET_MS` themselves.

The engine-level gap this docstring used to describe — a cold model load
blowing the 300ms budget and yielding zero entries — is fixed (GH #92). The
recall engine now admits a stream only if it can complete, and
`scripts/test_recall_stream_admission.py` pins that contract. What still isn't
covered here is the hook driving a vault large enough to exercise it.

Run: python3 scripts/test_memory_recall_prompt_submit_hook.py
Skipped on non-POSIX (bash hook).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK_DIR = _REPO / "harness" / "hooks" / "memory-recall-prompt-submit"
_HOOK = _HOOK_DIR / "memory-recall-prompt-submit.sh"
_HOOK_DOC = _HOOK_DIR / "hook.md"
_FRAGMENT = _HOOK_DIR / "settings-fragment-bash.json"
_RECALL_PY = _REPO / "harness" / "skills" / "memory" / "scripts" / "recall.py"

_SENTINEL = "PROMPT_RECALL_SENTINEL_BODY"
# A term that appears in the fixture entry and in the prompt, and nowhere else
# in the vault — so a hit proves the engine matched THIS prompt rather than
# emitting the vault wholesale.
_QUERY_TERM = "quokkaflange"
_PROMPT = f"how does the {_QUERY_TERM} protocol handle widget alignment?"
# Seeded alongside, sharing no tokens with the prompt. A run that surfaces it
# is dumping the vault, not recalling against the query.
_DISTRACTOR_SLUG = "unrelated-entry"


def _read_declared_budget_ms() -> int:
    """The prompt-submit time budget the recall engine enforces, read from source.

    Regex over the source rather than an import: the test drives the hook as a
    black box, and recall.py's sibling modules aren't on this process's path.
    """
    src = _RECALL_PY.read_text(encoding="utf-8")
    m = re.search(r"^PROMPT_SUBMIT_BUDGET_MS\s*=\s*(\d+)", src, re.MULTILINE)
    assert m, "PROMPT_SUBMIT_BUDGET_MS not found in recall.py"
    return int(m.group(1))


def _read_host_timeout_s() -> int:
    """The per-invocation timeout the installed settings fragment gives the hook."""
    frag = json.loads(_FRAGMENT.read_text(encoding="utf-8"))
    return frag["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"]


_BUDGET_MS = _read_declared_budget_ms()

# What the behavioral tests run under. Ten times the production budget and two
# orders of magnitude above the fixed cost a recall pays before its corpus walk
# starts (25-42ms on an idle
# M-series Mac, more under CI load), but still below the engine's
# VEC_COLD_EMBED_MIN_BUDGET_MS, so the vec half is declined on affordability
# exactly as in production rather than buying a real cold model load.
#
# Without this the injection assertions race that fixed cost: when it consumes
# the whole budget, the corpus walk is discarded before entry 0 and the hook
# prints nothing, so `assertIn(_SENTINEL, r.stdout)` fails against ''. Observed
# on Windows CI (PR #417) and on Linux CI (PR #418) — it is not platform
# specific, and the sibling library-level test in test_recall_stream_admission
# hit the same wall with no hook or subprocess involved at all.
_TEST_BUDGET_MS = 3000
_HOST_TIMEOUT_S = _read_host_timeout_s()


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestMemoryRecallPromptSubmitHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "personal").mkdir(parents=True)
        self.proj = self.root / "proj"
        (self.proj / ".harness").mkdir(parents=True)
        # Fake HOME → .agentm-config.json points the memory-script resolver at THIS repo.
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ── fixture ──────────────────────────────────────────────────────────────

    def _entry(self, body: str, *, name: str) -> str:
        return (
            f"---\nname: {name}\nkind: convention\n"
            f"description: a fixture entry\n---\n\n{body}\n"
        )

    def _seed_recallable(self, *, always_load: bool = False) -> None:
        """Seed the entry the prompt matches, plus a distractor that it doesn't.

        `always_load=True` puts the matching entry in the always-load directory
        instead — the SessionStart hook already injected it, so prompt-submit
        must dedup it away.
        """
        rel = Path("personal") / "_always-load" if always_load else Path("personal")
        target = self.vault / rel
        target.mkdir(parents=True, exist_ok=True)
        (target / "matching-entry.md").write_text(
            self._entry(
                f"{_SENTINEL} the {_QUERY_TERM} protocol governs widget alignment.",
                name="matching-entry",
            ),
            encoding="utf-8",
        )
        (self.vault / "personal" / f"{_DISTRACTOR_SLUG}.md").write_text(
            self._entry(
                "Sourdough hydration ratios for a cold winter kitchen.",
                name=_DISTRACTOR_SLUG,
            ),
            encoding="utf-8",
        )

    def _env(self, with_vault: bool = True, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        # Never let a test write the operator's real recall ledger — see
        # recall_counter.default_history_path().
        env["AGENTM_RECALL_HISTORY"] = str(self.root / "recall-history.jsonl")
        # Behavioral tests assert what recall INJECTS, and the production budget
        # is not part of that claim -- it is a tuning constant they were racing.
        # The two budget-semantics tests below opt back to _BUDGET_MS explicitly.
        # See _TEST_BUDGET_MS.
        env["RECALL_BUDGET_MS"] = str(_TEST_BUDGET_MS)
        if with_vault:
            env["MEMORY_VAULT_PATH"] = str(self.vault)
        else:
            env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _run_hook(self, env: dict, prompt: str | None = _PROMPT,
                  raw_payload: str | None = None):
        if raw_payload is None:
            payload: dict = {"hookEventName": "UserPromptSubmit", "session_id": "sess-1",
                             "cwd": str(self.proj)}
            if prompt is not None:
                payload["prompt"] = prompt
            raw_payload = json.dumps(payload)
        return subprocess.run(
            ["bash", str(_HOOK)], input=raw_payload, env=env,
            cwd=str(self.proj), capture_output=True, text=True,
        )

    def _time_hook(self, env: dict, runs: int = 3):
        """Fastest of `runs` invocations, as (elapsed_seconds, CompletedProcess).

        Fastest rather than mean: the floor is the machine's honest cost for the
        work, where the mean also carries whatever else the box was doing.
        """
        best: tuple[float, subprocess.CompletedProcess] | None = None
        for _ in range(runs):
            t0 = time.monotonic()
            r = self._run_hook(env)
            elapsed = time.monotonic() - t0
            if best is None or elapsed < best[0]:
                best = (elapsed, r)
        assert best is not None
        return best

    # ── fires ────────────────────────────────────────────────────────────────

    def test_fires_recall_injects_the_matching_entry(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(_SENTINEL, r.stdout)               # recall actually ran
        self.assertIn("MemoryVault — recall hits for your prompt", r.stdout)
        self.assertIn("### matching-entry", r.stdout)    # per-entry header block
        self.assertIn("Loaded 1 relevant entries: matching-entry", r.stderr)

    def test_recall_is_scoped_to_the_prompt(self) -> None:
        # The distractor shares no tokens with the prompt. Surfacing it would
        # mean the hook injects the vault rather than the query's matches.
        self._seed_recallable()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_DISTRACTOR_SLUG, r.stdout)

    def test_the_prompt_field_is_what_drives_recall(self) -> None:
        # Same vault, a prompt sharing no token with either entry → no
        # injection. Pins that the hook queries with the payload's `prompt`,
        # rather than emitting matches for anything it can find. The prompt
        # avoids the entries' frontmatter vocabulary too (name / kind /
        # description / convention / fixture / entry), which recall reads as
        # searchable content like any other line — and any word of three or
        # more characters counts, stopwords included (recall.py:_MIN_TOKEN_LEN).
        self._seed_recallable()
        r = self._run_hook(self._env(), prompt="please summarize yesterday's kubernetes rollout")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_SENTINEL, r.stdout)
        self.assertIn("Loaded 0 relevant entries", r.stderr)

    def test_always_load_entries_are_deduped_not_reinjected(self) -> None:
        # The SessionStart hook already put always-load entries in context;
        # re-injecting them on every prompt is the waste this dedup prevents.
        self._seed_recallable(always_load=True)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_SENTINEL, r.stdout)
        self.assertIn("Loaded 0 relevant entries", r.stderr)

    # ── silent + non-blocking (both modes) ───────────────────────────────────

    def test_no_vault_is_silent_but_exits_zero(self) -> None:
        # Repo-local / no-vault mode: nothing to recall, nothing emitted, and
        # the prompt goes through untouched.
        self._seed_recallable()  # present, but unreachable without MEMORY_VAULT_PATH
        r = self._run_hook(self._env(with_vault=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_missing_vault_path_is_silent_but_exits_zero(self) -> None:
        r = self._run_hook(self._env(MEMORY_VAULT_PATH=str(self.root / "gone")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("vault path not found", r.stderr)

    def test_recall_engine_error_never_blocks(self) -> None:
        # A vault path that is a file, not a directory — the engine raises
        # partway through. The catch-all must turn that into a stderr note and
        # exit 0, never a traceback the host reads as a block.
        broken = self.root / "vault-is-a-file"
        broken.write_text("not a vault\n", encoding="utf-8")
        r = self._run_hook(self._env(MEMORY_VAULT_PATH=str(broken)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("recall engine error", r.stderr)

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        # Bare HOME (no .agentm-config.json) + project has no .claude/skills/ →
        # recall.py is unresolvable → the hook exits 0 before recall, silently.
        bare = self.root / "barehome"
        bare.mkdir()
        self._seed_recallable()
        r = self._run_hook(self._env(HOME=str(bare)))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_malformed_stdin_is_nonblocking(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env(), raw_payload="this is not json{")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("no prompt on stdin", r.stderr)

    def test_payload_without_a_prompt_field_is_nonblocking(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env(), prompt=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("no prompt on stdin", r.stderr)

    def test_empty_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_recall_never_writes_the_vault(self) -> None:
        # hook.md: "Never writes to the vault. Pure read-only." Heat and
        # lifecycle tracking do write their own sidecars under _meta/, so the
        # invariant that matters is that no seeded ENTRY changes.
        self._seed_recallable()
        entries = sorted((self.vault / "personal").rglob("*.md"))
        before = {p: p.read_bytes() for p in entries}
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, f"{path} was rewritten")

    # ── interactive time budget ──────────────────────────────────────────────

    def test_declared_time_budget_is_consistent_across_hook_and_engine(self) -> None:
        # Three places state this hook's budget; a change to one that misses the
        # others leaves the docs lying about the interactive cost.
        doc = _HOOK_DOC.read_text(encoding="utf-8")
        m = re.search(r"\*\*Time budget:\*\*\s*(\d+)\s*ms", doc)
        self.assertIsNotNone(m, "hook.md states no time budget")
        self.assertEqual(int(m.group(1)), _BUDGET_MS)

        script = _HOOK.read_text(encoding="utf-8")
        m = re.search(r"Hard\s+(\d+)ms time budget", script)
        self.assertIsNotNone(m, "the hook script's header states no time budget")
        self.assertEqual(int(m.group(1)), _BUDGET_MS)

    def test_the_budget_env_reaches_the_engine_through_the_hook(self) -> None:
        # _env()'s widened budget is worth nothing unless the hook forwards the
        # variable, and a hook that silently stopped would just restore the
        # flake rather than fail anything. So starve it and require the
        # starvation to bite: at 1ms the corpus walk is discarded before entry 0
        # and the hook prints nothing. If the environment stops reaching the
        # engine, this falls back to the 300ms default, the entry is injected,
        # and this fails.
        #
        # Starving is the robust direction to assert in — the fixed pre-walk
        # cost is tens of milliseconds on any machine, so a 1ms deadline has
        # always already elapsed. Racing the other way is what flaked.
        self._seed_recallable()
        r = self._run_hook(self._env(RECALL_BUDGET_MS="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_the_host_timeout_leaves_room_for_the_declared_budget(self) -> None:
        # The installed fragment's timeout is the host's hard kill. A budget at
        # or above it would mean a within-budget recall could still be killed
        # mid-flight.
        self.assertGreater(_HOST_TIMEOUT_S * 1000, _BUDGET_MS)

    def test_completes_well_inside_the_host_timeout(self) -> None:
        # Exceeding the fragment's timeout is what actually costs the operator a
        # prompt, so that is the number this bounds against — not a wall-clock
        # figure invented here.
        #
        # Runs under the production budget, not the fixture's widened one: this
        # bounds what the operator's own configuration costs.
        self._seed_recallable()
        elapsed, r = self._time_hook(self._env(RECALL_BUDGET_MS=str(_BUDGET_MS)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(
            elapsed, _HOST_TIMEOUT_S / 2,
            f"hook took {elapsed:.2f}s against a {_HOST_TIMEOUT_S}s host timeout",
        )

    def test_budget_overrun_is_reported_not_hidden(self) -> None:
        # The budget is soft: recall short-circuits between steps and reports an
        # overrun on the transparency line rather than killing the query. So the
        # invariant is agreement — the WARNING is present exactly when the recall
        # phase actually ran long, on whatever machine this is.
        #
        # `baseline` is the same hook with a nonexistent vault: identical
        # interpreter start, import, and resolution cost, returning immediately
        # before any recall work. Subtracting it leaves the recall phase, which
        # is what the budget clock actually covers.
        #
        # Both runs pin RECALL_BUDGET_MS to the declared budget. This test
        # correlates the measured phase against _BUDGET_MS, so it has to be the
        # budget the engine actually ran under — the fixture's widened default
        # would leave it comparing a 3000ms run to a 300ms threshold.
        self._seed_recallable()
        budget_env = {"RECALL_BUDGET_MS": str(_BUDGET_MS)}
        baseline, _ = self._time_hook(
            self._env(MEMORY_VAULT_PATH=str(self.root / "gone"), **budget_env))
        total, r = self._time_hook(self._env(**budget_env))
        self.assertEqual(r.returncode, 0, r.stderr)

        recall_phase = max(0.0, total - baseline)
        budget_s = _BUDGET_MS / 1000.0
        warned = "time budget exceeded" in r.stderr
        detail = (f"recall phase {recall_phase * 1000:.0f}ms vs {_BUDGET_MS}ms budget; "
                  f"stderr: {r.stderr.strip()}")

        # Only assert outside the band where measurement error could straddle
        # the budget — an external clock can't resolve a 310ms internal run from
        # a 290ms one, and a test that pretends otherwise is a flake.
        if recall_phase > 2 * budget_s:
            self.assertTrue(warned, f"ran long but reported no overrun — {detail}")
        elif recall_phase < 0.5 * budget_s:
            self.assertFalse(warned, f"reported an overrun it did not have — {detail}")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
