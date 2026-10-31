#!/usr/bin/env python3
"""Antigravity registration of agentm's recall hooks (task: automatic recall on agy).

Two halves.

GeneratorTests pin what `scripts/emit_agy_hooks.py` writes from the installed hooks:
which hooks reach agy, on which agy event, through which command.

AgyChainTests run the *real* hook scripts through the *real* vendored adapter, the
way agy does: take a handler's command out of the generated `hooks.json`, run it with
`sh -c` from the plugin directory, feed it the payload agy 1.4 documents on stdin
(camelCase `conversationId` / `workspacePaths` / `transcriptPath` / `lastUserInput`),
and read the one JSON object it prints. Nothing is stubbed on the path under test; the
only stand-ins are the two memory scripts the marker proofs need to observe
(reflect.py, orchestration_briefing.py), and they are planted in the *workspace*,
which the hooks resolve first, so the rest of the chain stays the shipped code.

The proof that matters is the marker one. memory-recall-session-start writes
`.harness/session-id-<sid>.start` naming the host's transcript, and memory-reflect-idle
later reflects on that file in Claude Code's format with --route aimed at the real
vault. An agy transcript is a different format. TestMarkerProof shows an agy session
creates no such marker, shows the idle job then has nothing to hand reflect.py, and
-- so the test can fail -- shows the same payload with both guards removed does leave
a marker that the idle job hands to reflect.py. Without that control a green result
could mean only that the hook never ran.

Hermetic: a fake HOME whose `.claude/.agentm-config.json` resolves the memory scripts
to THIS repo, a fixture vault, and a private TMPDIR (the adapter keeps its
once-per-message state there). The live ~/.claude and ~/.gemini are never read or
written.

Run: python3 scripts/test_agy_recall_hooks.py
Skipped on non-POSIX (bash hooks, `sh -c`).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOKS = _REPO / "harness" / "hooks"
_IDLE_HOOK = _HOOKS / "memory-reflect-idle" / "memory-reflect-idle.sh"
_ADAPTER = _REPO / "adapters" / "antigravity" / "hooks" / "_agy_hook_adapter.py"

_spec = importlib.util.spec_from_file_location("emit_agy_hooks", _HERE / "emit_agy_hooks.py")
emit = importlib.util.module_from_spec(_spec)
sys.modules["emit_agy_hooks"] = emit
_spec.loader.exec_module(emit)

# The three hooks that declare antigravity, and the agy event each is registered on.
_REGISTERED = {
    "memory-recall-session-start": "SessionStart",
    "project-brief-session-start": "SessionStart",
    "memory-recall-prompt-submit": "PreInvocation",
}
# Claude Code's alone: every one is either bound to Claude's transcript, to its
# tool/compaction events, or is reached only as a sibling of a registered hook.
_CLAUDE_ONLY = (
    "memory-reflect-stop", "memory-reflect-idle", "harness-context-session-start",
    "compaction-marker", "compaction-reanchor", "verify-dispatch", "vault-worktree-guard",
)

_ALWAYS_LOAD_SENTINEL = "ALWAYS_LOAD_SENTINEL_BODY"
_RECALL_SENTINEL = "PROMPT_RECALL_SENTINEL_BODY"
_QUERY_TERM = "quokkaflange"
_PROMPT = f"how does the {_QUERY_TERM} protocol handle widget alignment?"


@unittest.skipIf(os.name == "nt", "the generator writes `sh -c` lines and the PowerShell installer has no counterpart")
class GeneratorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        (self.prefix / "hooks").symlink_to(_HOOKS)
        self.out = self.root / "plugin"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _emit(self, version: str = "9.9.9") -> dict:
        rc = emit.emit(self.prefix, self.out, version)
        self.assertEqual(rc, 0)
        return json.loads((self.out / "hooks.json").read_text(encoding="utf-8"))

    def test_registers_the_declared_hooks_on_the_right_agy_events(self) -> None:
        hooks = self._emit()
        self.assertEqual(set(hooks), set(_REGISTERED))
        for name, event in _REGISTERED.items():
            self.assertTrue(hooks[name]["enabled"], name)
            self.assertEqual(
                [k for k in hooks[name] if k != "enabled"], [event], name)
            self.assertEqual(len(hooks[name][event]), 1, name)

    def test_claude_only_hooks_are_not_registered(self) -> None:
        hooks = self._emit()
        for name in _CLAUDE_ONLY:
            self.assertNotIn(name, hooks)

    def test_every_handler_carries_the_host_the_off_switch_and_the_adapter(self) -> None:
        hooks = self._emit()
        for name, event in _REGISTERED.items():
            h = hooks[name][event][0]
            self.assertEqual(h["type"], "command")
            cmd = h["command"]
            self.assertIn("AGENTM_HOST=antigravity", cmd, name)
            self.assertIn('[ "${AGENTM_AGY_RECALL:-}" = off ]', cmd, name)
            self.assertIn('RECALL_TOKEN_BUDGET="${RECALL_TOKEN_BUDGET:-10000}"', cmd, name)
            self.assertIn("python3 ./hooks/_agy_hook_adapter.py", cmd, name)

    def test_handlers_run_the_installed_script_and_keep_the_fragments_timeout(self) -> None:
        hooks = self._emit()
        for name, event in _REGISTERED.items():
            h = hooks[name][event][0]
            script = self.prefix / "hooks" / name / f"{name}.sh"
            self.assertTrue(h["command"].endswith(f"bash {script}"), h["command"])
            frag = json.loads((_HOOKS / name / "settings-fragment-bash.json").read_text("utf-8"))
            claude_event = "UserPromptSubmit" if event == "PreInvocation" else "SessionStart"
            self.assertEqual(h["timeout"], frag["hooks"][claude_event][0]["hooks"][0]["timeout"])
            # The Claude event name is what the adapter is told: it maps payloads by it.
            self.assertIn(f"_agy_hook_adapter.py {claude_event} bash", h["command"])

    def test_the_vendored_adapter_ships_whole_and_the_manifest_names_the_plugin(self) -> None:
        self._emit(version="9.9.9")
        self.assertEqual((self.out / "hooks" / "_agy_hook_adapter.py").read_bytes(),
                         _ADAPTER.read_bytes())
        manifest = json.loads((self.out / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "agentm")
        self.assertEqual(manifest["version"], "9.9.9")

    def test_a_relative_prefix_still_gives_absolute_script_paths(self) -> None:
        # agy runs a handler from the plugin's directory; a relative script path
        # would resolve against that and the hook would silently do nothing.
        (self.root / "rel").symlink_to(self.prefix)
        here = os.getcwd()
        os.chdir(self.root)
        try:
            self.assertEqual(emit.main(["--prefix", "rel", "--out", str(self.out)]), 0)
        finally:
            os.chdir(here)
        hooks = json.loads((self.out / "hooks.json").read_text(encoding="utf-8"))
        for name, event in _REGISTERED.items():
            cmd = hooks[name][event][0]["command"]
            self.assertIn("bash /", cmd)
            self.assertNotIn("bash rel", cmd)

    def test_a_rebuild_replaces_the_plugin_rather_than_adding_to_it(self) -> None:
        self._emit()
        (self.out / "stale.txt").write_text("left by an earlier build", encoding="utf-8")
        self._emit()
        self.assertFalse((self.out / "stale.txt").exists())

    def _fake_prefix(self, hook_md_hosts: str, fragment: dict | None) -> Path:
        prefix = self.root / "fake"
        d = prefix / "hooks" / "odd-hook"
        d.mkdir(parents=True)
        (d / "hook.md").write_text(
            f"---\nname: odd-hook\nsupported_hosts: {hook_md_hosts}\n---\n", encoding="utf-8")
        (d / "odd-hook.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        if fragment is not None:
            (d / "settings-fragment-bash.json").write_text(json.dumps(fragment), encoding="utf-8")
        return prefix

    def test_a_declared_hook_on_an_event_agy_has_no_flat_twin_for_is_an_error(self) -> None:
        # A registration that quietly lacks a handler is how a guard goes missing.
        prefix = self._fake_prefix("[claude-code, antigravity]", {"hooks": {"PreToolUse": [
            {"matcher": ".*", "hooks": [{"type": "command", "command": "bash x.sh"}]}]}})
        with self.assertRaises(emit.EmitError):
            emit.build_hooks(prefix)
        self.assertEqual(emit.main(["--prefix", str(prefix), "--out", str(self.out)]), 1)

    def test_a_declared_hook_with_no_fragment_is_an_error(self) -> None:
        prefix = self._fake_prefix("[antigravity]", None)
        with self.assertRaises(emit.EmitError):
            emit.build_hooks(prefix)

    def test_nothing_declared_means_nothing_to_register(self) -> None:
        prefix = self._fake_prefix("[claude-code]", {"hooks": {"SessionStart": []}})
        self.assertEqual(emit.main(["--prefix", str(prefix), "--out", str(self.out)]), 3)
        self.assertFalse(self.out.exists())

    def test_each_registered_hook_declares_antigravity_in_its_own_doc(self) -> None:
        for name in _REGISTERED:
            self.assertIn("antigravity", emit.supported_hosts(_HOOKS / name / "hook.md"), name)
        for name in _CLAUDE_ONLY:
            self.assertNotIn("antigravity", emit.supported_hosts(_HOOKS / name / "hook.md"), name)


@unittest.skipIf(os.name == "nt", "bash hooks run through `sh -c` — POSIX only")
class _AgyFixture(unittest.TestCase):
    """The fake machine both chain test classes run on (it defines no tests itself)."""

    def setUp(self) -> None:
        # mkdtemp + ignore_errors: the idle hook launches a detached chain.
        self.root = Path(tempfile.mkdtemp(prefix="agentm-agy-hooks-test-"))
        self.vault = self.root / "vault"
        (self.vault / "memory").mkdir(parents=True)
        # Neutralize the detached idle chain, as the idle hook's own tests do.
        desk = self.vault / "projects" / "agentm" / "desk"
        desk.mkdir(parents=True)
        (desk / "auto-orchestration-config.md").write_text(
            "```settings\nenable_idle_chain = false\n```\n", encoding="utf-8")
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8")
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        prefix = self.root / "prefix"
        prefix.mkdir()
        (prefix / "hooks").symlink_to(_HOOKS)
        self.plugin = self.root / "plugin"
        self.assertEqual(emit.emit(prefix, self.plugin, "0.0.0"), 0)
        self.hooks = json.loads((self.plugin / "hooks.json").read_text(encoding="utf-8"))
        # agy's transcript lives under ~/.gemini/…; this one is under the fake home.
        self.agy_transcript = (self.fake_home / ".gemini" / "antigravity-cli" / "conversations"
                               / "conv-1" / "transcript_full.jsonl")
        self.agy_transcript.parent.mkdir(parents=True)
        self.agy_transcript.write_text(
            json.dumps({"step_index": 0, "source": "USER", "type": "USER_INPUT",
                        "status": "DONE", "content": "hello"}) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _env(self, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home), "TMPDIR": str(self.tmpdir),
               "MEMORY_VAULT_PATH": str(self.vault), "MEMORY_ROOT": str(self.vault),
               "AGENTM_RECALL_HISTORY": str(self.root / "recall-history.jsonl"),
               "RECALL_BUDGET_MS": "3000", "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("AGENTM_INSTALL_PREFIX", "AGENTM_HOST", "AGENTM_AGY_RECALL"):
            env.pop(k, None)
        env.update(over)
        return env

    def _command(self, hook: str) -> str:
        (_event, handlers), = ((k, v) for k, v in self.hooks[hook].items() if k != "enabled")
        return handlers[0]["command"]

    def _payload(self, *, prompt: str = "", transcript: Path | None = None,
                 conversation: str = "conv-1") -> dict:
        p = {"conversationId": conversation, "workspacePaths": [str(self.ws)],
             "transcriptPath": str(transcript or self.agy_transcript),
             "artifactDirectoryPath": str(self.root / "artifacts"), "modelName": "auto"}
        if prompt:
            p["lastUserInput"] = prompt
        return p

    def _run(self, command: str, payload: dict, **env_over) -> subprocess.CompletedProcess:
        """Run one handler the way agy does: `sh -c`, cwd = the plugin root, JSON on stdin."""
        return subprocess.run(["sh", "-c", command], input=json.dumps(payload),
                              env=self._env(**env_over), cwd=str(self.plugin),
                              capture_output=True, text=True)

    def _answer(self, r: subprocess.CompletedProcess) -> dict:
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        self.assertTrue(out, f"agy needs one JSON object on stdout; got nothing. stderr: {r.stderr}")
        ans = json.loads(out)
        self.assertIsInstance(ans, dict)
        return ans

    def _seed_always_load(self) -> None:
        d = self.vault / "memory" / "_always-load"
        d.mkdir(parents=True, exist_ok=True)
        (d / "conv.md").write_text(
            f"---\nname: conv\ndescription: a test convention\nmetadata:\n  type: reference\n---\n\n"
            f"{_ALWAYS_LOAD_SENTINEL}\n", encoding="utf-8")

    def _seed_recallable(self) -> None:
        (self.vault / "memory" / "matching-entry.md").write_text(
            f"---\nname: matching-entry\nkind: convention\ndescription: a fixture entry\n---\n\n"
            f"{_RECALL_SENTINEL} the {_QUERY_TERM} protocol governs widget alignment.\n",
            encoding="utf-8")
        (self.vault / "memory" / "unrelated-entry.md").write_text(
            "---\nname: unrelated-entry\nkind: convention\ndescription: a fixture entry\n---\n\n"
            "Sourdough hydration ratios for a cold winter kitchen.\n", encoding="utf-8")

    def _markers(self) -> list[Path]:
        return sorted(p for d in (self.ws, self.plugin) for p in d.glob(".harness/session-id-*"))


@unittest.skipIf(os.name == "nt", "bash hooks run through `sh -c` — POSIX only")
class AgyChainTests(_AgyFixture):

    # ── the hooks deliver what agy needs ─────────────────────────────────────

    def test_session_start_recall_arrives_as_a_system_message(self) -> None:
        self._seed_always_load()
        ans = self._answer(self._run(self._command("memory-recall-session-start"), self._payload()))
        (step,) = ans["injectSteps"]
        # systemMessage persists in the conversation; agy wants it nested, not a bare string.
        self.assertIn(_ALWAYS_LOAD_SENTINEL, step["systemMessage"]["systemMessage"])

    def test_per_message_recall_arrives_as_a_one_turn_message_matching_the_prompt(self) -> None:
        self._seed_recallable()
        cmd = self._command("memory-recall-prompt-submit")
        ans = self._answer(self._run(cmd, self._payload(prompt=_PROMPT)))
        (step,) = ans["injectSteps"]
        self.assertIsInstance(step["ephemeralMessage"], str)   # one turn, a plain string
        self.assertIn(_RECALL_SENTINEL, step["ephemeralMessage"])
        self.assertNotIn("unrelated-entry", step["ephemeralMessage"])

    def test_recall_runs_once_per_user_message_though_agy_fires_before_every_model_call(self) -> None:
        self._seed_recallable()
        cmd = self._command("memory-recall-prompt-submit")
        first = self._answer(self._run(cmd, self._payload(prompt=_PROMPT)))
        self.assertIn("injectSteps", first)
        # agy's PreInvocation fires again before each model call of the same turn,
        # carrying the same lastUserInput: the hook must say nothing the second time.
        for _ in range(2):
            self.assertEqual(self._answer(self._run(cmd, self._payload(prompt=_PROMPT))), {})
        # A different message is a new turn and recalls again.
        again = self._answer(self._run(cmd, self._payload(prompt=_PROMPT + " and then?")))
        self.assertIn("injectSteps", again)

    def test_a_prompt_nothing_matches_adds_nothing(self) -> None:
        self._seed_recallable()
        cmd = self._command("memory-recall-prompt-submit")
        self.assertEqual(self._answer(self._run(cmd, self._payload(prompt="zzzz yyyy xxxx"))), {})

    def test_project_brief_answers_in_agys_shape(self) -> None:
        ans = self._answer(self._run(self._command("project-brief-session-start"), self._payload()))
        self.assertLessEqual(set(ans), {"injectSteps"})
        for step in ans.get("injectSteps", []):
            self.assertIn("systemMessage", step)

    def test_an_oversized_always_load_set_is_trimmed_by_the_engine_before_agy_cuts_it(self) -> None:
        # Measured on agy 1.4.12: one injected message is cut at 49,309 characters,
        # head kept, tail replaced by "<truncated N bytes>". The registered budget keeps
        # the engine under that, so it drops whole entries and names them instead.
        d = self.vault / "memory" / "_always-load"
        d.mkdir(parents=True, exist_ok=True)
        for slug in ("first-big", "second-big"):
            (d / f"{slug}.md").write_text(
                f"---\nname: {slug}\ndescription: big\nmetadata:\n  type: reference\n---\n\n"
                + ("padding padding padding padding\n" * 800), encoding="utf-8")   # ~25 KB each
        ans = self._answer(self._run(self._command("memory-recall-session-start"), self._payload()))
        text = ans["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertLess(len(text), 46_000, "agy would cut this mid-entry")
        self.assertIn("recall truncated", text)         # the engine's own note, which the model reads
        self.assertIn("always-load", text)

    def test_a_budget_already_in_the_environment_wins(self) -> None:
        d = self.vault / "memory" / "_always-load"
        d.mkdir(parents=True, exist_ok=True)
        (d / "only.md").write_text(
            "---\nname: only\ndescription: d\nmetadata:\n  type: reference\n---\n\n"
            + ("padding padding padding padding\n" * 800), encoding="utf-8")        # ~6.4k est. tokens
        cmd = self._command("memory-recall-session-start")
        tight = self._answer(self._run(cmd, self._payload(), RECALL_TOKEN_BUDGET="100"))
        self.assertIn("recall truncated", tight["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_the_off_switch_answers_empty_and_runs_no_hook(self) -> None:
        self._seed_always_load()
        r = self._run(self._command("memory-recall-session-start"), self._payload(),
                      AGENTM_AGY_RECALL="off")
        self.assertEqual(self._answer(r), {})
        self.assertNotIn(_ALWAYS_LOAD_SENTINEL, r.stdout)
        self.assertEqual(self._markers(), [])


@unittest.skipIf(os.name == "nt", "bash hooks run through `sh -c` — POSIX only")
class TestMarkerProof(_AgyFixture):
    """An agy session cannot create a marker the idle job will mis-read."""

    def setUp(self) -> None:
        super().setUp()
        # The two memory scripts whose effects the proofs observe. reflect.py is
        # planted in the workspace: the idle job is Claude Code's, and Claude Code
        # looks for `.claude/skills/memory/scripts/<name>` under the cwd first. The
        # briefing is planted in an install prefix instead, because an agy session
        # never looks under the workspace (TestWorkspaceIsData) — and it is the only
        # script there, so recall.py still resolves to the shipped one through the
        # fake HOME's config.
        scripts = self.ws / ".claude" / "skills" / "memory" / "scripts"
        scripts.mkdir(parents=True)
        self.reflect_log = self.root / "reflect-calls.log"
        self.briefing_log = self.root / "briefing-calls.log"
        (scripts / "reflect.py").write_text(
            "import sys\nopen(%r, 'a').write(sys.argv[1] + '\\n')\n" % str(self.reflect_log),
            encoding="utf-8")
        self.bprefix = self.root / "bprefix"
        (self.bprefix / "skills" / "memory" / "scripts").mkdir(parents=True)
        (self.bprefix / "skills" / "memory" / "scripts" / "orchestration_briefing.py").write_text(
            "open(%r, 'a').write('ran\\n')\nprint('BRIEFING_STUB_RAN')\n" % str(self.briefing_log),
            encoding="utf-8")
        shutil.copyfile(self.fake_home / ".claude" / ".agentm-config.json",
                        self.bprefix / ".agentm-config.json")

    def _idle_sweep(self) -> subprocess.CompletedProcess:
        # Threshold 0: every .start marker counts as past the idle threshold.
        return subprocess.run(["bash", str(_IDLE_HOOK)], input="", cwd=str(self.ws),
                              env=self._env(MEMORY_IDLE_THRESHOLD_SEC="0"),
                              capture_output=True, text=True)

    def _reflected(self) -> list[str]:
        return self.reflect_log.read_text(encoding="utf-8").split() if self.reflect_log.exists() else []

    def _without(self, command: str, token: str) -> str:
        self.assertIn(token, command)
        return command.replace(token, "", 1)

    # ── the proof ────────────────────────────────────────────────────────────

    def test_an_agy_session_start_leaves_no_marker_and_the_idle_job_has_nothing_to_reflect(self) -> None:
        self._seed_always_load()
        for hook in ("memory-recall-session-start", "project-brief-session-start"):
            self._answer(self._run(self._command(hook), self._payload()))
        self.assertEqual(self._markers(), [])
        r = self._idle_sweep()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._reflected(), [], "the idle job handed reflect.py something")

    def test_the_transcript_location_alone_blocks_the_marker(self) -> None:
        # A registration that lost AGENTM_HOST (an old generated plugin, a hand edit)
        # must still not leave a marker: an agy transcript is under ~/.gemini.
        cmd = self._without(self._command("memory-recall-session-start"), "AGENTM_HOST=antigravity ")
        self.assertNotIn("AGENTM_HOST", cmd)
        self._answer(self._run(cmd, self._payload()))
        self.assertEqual(self._markers(), [])

    def test_the_declared_host_alone_blocks_the_marker(self) -> None:
        # …and the converse: a transcript path that does not look like agy's,
        # under the declared host, still leaves nothing.
        elsewhere = self.root / "conversations" / "conv-1" / "log.jsonl"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("{}\n", encoding="utf-8")
        self._answer(self._run(self._command("memory-recall-session-start"),
                               self._payload(transcript=elsewhere)))
        self.assertEqual(self._markers(), [])

    def test_control_without_either_guard_the_marker_is_written_and_the_idle_job_mines_it(self) -> None:
        # What the guards prevent. The same agy payload, the host variable removed
        # and the transcript somewhere that does not look like agy's: the hook cannot
        # tell it from Claude Code's, writes the marker, and the idle job hands the
        # agy file to reflect.py. If this stops holding, the tests above prove nothing.
        elsewhere = self.root / "conversations" / "conv-1" / "log.jsonl"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("{}\n", encoding="utf-8")
        cmd = self._without(self._command("memory-recall-session-start"), "AGENTM_HOST=antigravity ")
        self._answer(self._run(cmd, self._payload(transcript=elsewhere)))
        (marker,) = self._markers()
        self.assertIn(f"transcript: {elsewhere}", marker.read_text(encoding="utf-8"))
        self._idle_sweep()
        self.assertEqual(self._reflected(), [str(elsewhere)])

    def test_claude_code_still_writes_its_marker(self) -> None:
        # The guard must not cost Claude Code its crash recovery: no host variable,
        # a Claude-shaped payload straight into the hook.
        hook = _HOOKS / "memory-recall-session-start" / "memory-recall-session-start.sh"
        claude_transcript = self.fake_home / ".claude" / "projects" / "-ws" / "sess-9.jsonl"
        r = subprocess.run(
            ["bash", str(hook)], cwd=str(self.ws), env=self._env(), capture_output=True, text=True,
            input=json.dumps({"session_id": "sess-9", "cwd": str(self.ws),
                              "transcript_path": str(claude_transcript), "source": "startup"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        marker = self.ws / ".harness" / "session-id-sess-9.start"
        self.assertIn(f"transcript: {claude_transcript}", marker.read_text(encoding="utf-8"))

    # ── the other thing Claude's alone: the shown-once briefing ──────────────

    def test_the_briefing_runs_for_claude_code_and_not_for_agy(self) -> None:
        hook = _HOOKS / "memory-recall-session-start" / "memory-recall-session-start.sh"
        prefix = {"AGENTM_INSTALL_PREFIX": str(self.bprefix)}
        claude = subprocess.run(
            ["bash", str(hook)], cwd=str(self.ws), env=self._env(**prefix), capture_output=True, text=True,
            input=json.dumps({"session_id": "sess-8", "cwd": str(self.ws)}))
        self.assertIn("BRIEFING_STUB_RAN", claude.stdout)       # the stub is reachable
        self.assertTrue(self.briefing_log.exists())
        self.briefing_log.unlink()
        r = self._run(self._command("memory-recall-session-start"), self._payload(), **prefix)
        self.assertNotIn("BRIEFING_STUB_RAN", r.stdout)
        self.assertFalse(self.briefing_log.exists(),
                         "an agy session ran the briefing and would use up its one showing")


@unittest.skipIf(os.name == "nt", "bash hooks run through `sh -c` — POSIX only")
class TestWorkspaceIsData(_AgyFixture):
    """A workspace opened in agy cannot supply the scripts a recall hook runs.

    The hooks look for `.claude/skills/memory/scripts/<name>` under the cwd first
    (Claude Code's project-scope install). On agy the cwd is whatever folder was
    opened, and agy runs a plugin's hooks without asking whether to trust it, so a
    cloned repository carrying its own recall.py would be executed at every session
    start and every message.
    """

    def setUp(self) -> None:
        super().setUp()
        scripts = self.ws / ".claude" / "skills" / "memory" / "scripts"
        scripts.mkdir(parents=True)
        self.ran = self.root / "workspace-code-ran.log"
        for name in ("recall.py", "orchestration_briefing.py"):
            (scripts / name).write_text(
                "open(%r, 'a').write(%r + '\\n')\n" % (str(self.ran), name), encoding="utf-8")

    def test_agy_sessions_never_run_code_the_workspace_ships(self) -> None:
        self._run(self._command("memory-recall-session-start"), self._payload())
        self._run(self._command("memory-recall-prompt-submit"), self._payload(prompt=_PROMPT))
        self.assertFalse(self.ran.exists(), "agy ran code shipped inside the opened workspace")

    def test_control_claude_code_does_find_the_project_scope_copy(self) -> None:
        # So the test above can fail: the planted scripts are reachable the way
        # Claude Code's own project-scope install is.
        for hook, payload in (
            ("memory-recall-session-start", {"session_id": "s", "cwd": str(self.ws)}),
            ("memory-recall-prompt-submit", {"prompt": _PROMPT, "cwd": str(self.ws)}),
        ):
            subprocess.run(["bash", str(_HOOKS / hook / f"{hook}.sh")], cwd=str(self.ws),
                           env=self._env(), input=json.dumps(payload),
                           capture_output=True, text=True)
        self.assertIn("recall.py", self.ran.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
