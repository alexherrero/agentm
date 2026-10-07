#!/usr/bin/env python3
"""The vault-worktree-guard hook refuses a git worktree in the vault, and only there.

On 2026-10-02 a background-task chip opened from a session in
`Vault/projects/pixelton` made a worktree inside the vault. Google Drive
uploaded it file by file, and the app's `extensions.worktreeConfig` key stopped
the daemon committing after its next restart (#859). The hook refuses the four
tool calls that can make one: the chip's `spawn_task`, `EnterWorktree`, a
subagent with worktree isolation, and a Bash command that adds a worktree.

Every case drives the shipped hook with the payload Claude Code sends. The
fixture vault is a git repository whose git directory sits outside it, behind a
`.git` pointer file, the way the live vault's does, and `MEMORY_ROOT` points
the resolver at it. A real agentm checkout, this one, and a sibling directory
whose name starts with the vault's are the "elsewhere" that must keep working.
The hook only judges a command and never runs it, so no case here makes a
worktree.

Run: python3 scripts/test_vault_worktree_guard.py
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

_REPO = Path(__file__).resolve().parent.parent
_HOOK_DIR = _REPO / "harness" / "hooks" / "vault-worktree-guard"
_HOOK_SH = _HOOK_DIR / "vault-worktree-guard.sh"
_HOOK_PS1 = _HOOK_DIR / "vault-worktree-guard.ps1"
_GUARD_PY = _HOOK_DIR / "vault_worktree_guard.py"

_spec = importlib.util.spec_from_file_location("vault_worktree_guard", _GUARD_PY)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

# The subagent tool, by the name the guard holds for it.
AGENT = guard.AGENT_TOOLS[0]


def _git(*args: str, cwd: Path, env: dict) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=str(cwd), env=env, check=True, capture_output=True)


class _Fixture(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        empty = self.root / "gitconfig"
        empty.write_text("", encoding="utf-8")
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH", "AGENTM_INSTALL_PREFIX",
                                 "GIT_DIR", "GIT_WORK_TREE")}
        self.env.update({"HOME": str(self.home), "GIT_CONFIG_GLOBAL": str(empty),
                         "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"})

        # The vault: its git directory lives outside it, behind a pointer file.
        self.vault = self.root / "vault"
        self.vault_git = self.root / "vault-git" / "vault.git"
        (self.vault / "projects" / "pixelton").mkdir(parents=True)
        (self.vault / "projects" / "pixelton" / "note.md").write_text("# note\n", encoding="utf-8")
        self.vault_git.parent.mkdir()
        _git("init", "-q", f"--separate-git-dir={self.vault_git}", str(self.vault), cwd=self.root, env=self.env)
        _git("add", "-A", cwd=self.vault, env=self.env)
        _git("commit", "-qm", "seed", cwd=self.vault, env=self.env)
        self.env["MEMORY_ROOT"] = str(self.vault)

        # Elsewhere: a code repository, and one whose path starts like the vault's.
        self.code = self.root / "code"
        self.lookalike = self.root / "vault-copy"
        for repo in (self.code, self.lookalike):
            repo.mkdir()
            (repo / "README.md").write_text("x\n", encoding="utf-8")
            _git("init", "-q", cwd=repo, env=self.env)
            _git("add", "-A", cwd=repo, env=self.env)
            _git("commit", "-qm", "seed", cwd=repo, env=self.env)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_hook(self, tool: str, tool_input: dict, cwd: Path, *, hook: Path = _HOOK_SH,
                 env: dict | None = None) -> subprocess.CompletedProcess:
        payload = {"session_id": "s", "transcript_path": "/dev/null", "hook_event_name": "PreToolUse",
                   "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}
        argv = ["bash", str(hook)] if hook.suffix == ".sh" else ["pwsh", "-NoProfile", "-File", str(hook)]
        return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                              env=env if env is not None else self.env, timeout=60)

    def assertRefused(self, proc: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(proc.returncode, 2, f"expected a refusal; stdout={proc.stdout!r} stderr={proc.stderr!r}")
        self.assertIn("Refused by vault-worktree-guard", proc.stderr)
        self.assertIn("#859", proc.stderr)
        for fragment in fragments:
            self.assertIn(fragment, proc.stderr)

    def assertAllowed(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 0, f"expected the call to pass; stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, "")


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TheVaultRefuses(_Fixture):

    def test_enter_worktree_from_inside_the_vault_is_refused_with_the_reason(self):
        proc = self.run_hook("EnterWorktree", {"name": "x"}, self.vault / "projects" / "pixelton")
        self.assertRefused(proc, "EnterWorktree", str(self.vault), "Google Drive", "extensions.worktreeConfig")

    def test_git_dash_c_vault_worktree_add_is_refused(self):
        proc = self.run_hook("Bash", {"command": f"git -C {self.vault} worktree add ../wt"}, self.code)
        self.assertRefused(proc, "adds a git worktree")

    def test_a_chip_spawned_from_a_vault_session_is_refused(self):
        # The 2026-10-02 route: the chip's session starts in a worktree under its cwd.
        proc = self.run_hook("mcp__ccd_session__spawn_task",
                             {"title": "Anchor the art direction", "prompt": "p", "tldr": "t"},
                             self.vault / "projects" / "pixelton")
        self.assertRefused(proc, "chip", "Pass `cwd`")

    def test_a_chip_pointed_at_the_vault_from_elsewhere_is_refused(self):
        proc = self.run_hook("mcp__ccd_session__spawn_task",
                             {"title": "t", "prompt": "p", "tldr": "t", "cwd": str(self.vault)}, self.code)
        self.assertRefused(proc, "chip")

    def test_a_worktree_isolated_subagent_in_the_vault_is_refused(self):
        proc = self.run_hook(AGENT, {"description": "d", "prompt": "p", "isolation": "worktree"}, self.vault)
        self.assertRefused(proc, 'isolation "worktree"')

    def test_cd_into_the_vault_then_add_is_refused_even_with_the_worktree_outside_it(self):
        # A worktree of the vault repository leaves the config key wherever it sits.
        proc = self.run_hook("Bash", {"command": f"cd {self.vault} && git worktree add {self.root}/elsewhere"},
                             self.code)
        self.assertRefused(proc)

    def test_a_worktree_of_another_repository_placed_under_the_vault_is_refused(self):
        proc = self.run_hook("Bash", {"command": f"git -C {self.code} worktree add {self.vault}/scratch"},
                             self.code)
        self.assertRefused(proc, str(self.vault / "scratch"))

    def test_git_dir_and_the_GIT_DIR_assignment_name_the_vault_repository(self):
        for command in (f"git --git-dir={self.vault_git} worktree add {self.root}/w1",
                        f"git --git-dir {self.vault_git} worktree add {self.root}/w2",
                        f"GIT_DIR={self.vault_git} git worktree add {self.root}/w3"):
            with self.subTest(command=command):
                self.assertRefused(self.run_hook("Bash", {"command": command}, self.code))

    def test_bash_dash_c_is_read_through(self):
        proc = self.run_hook("Bash", {"command": f"bash -c \"cd {self.vault} && git worktree add -b x ../y\""},
                             self.code)
        self.assertRefused(proc)

    def test_a_hash_inside_a_word_does_not_hide_the_command_after_it(self):
        proc = self.run_hook("Bash", {"command": f"echo a#b; git -C {self.vault} worktree add ../z"}, self.code)
        self.assertRefused(proc)

    def test_a_comment_line_does_not_join_the_next_line(self):
        proc = self.run_hook("Bash", {"command": f"cd {self.vault} # it's the vault\ngit worktree add ../z"},
                             self.code)
        self.assertRefused(proc)

    def test_a_symlink_to_the_vault_is_the_vault(self):
        link = self.root / "link-to-vault"
        link.symlink_to(self.vault)
        self.assertRefused(self.run_hook("EnterWorktree", {}, link / "projects"))
        self.assertRefused(self.run_hook("Bash", {"command": f"git -C {link} worktree add ../q"}, self.code))

    def test_the_vault_spelled_in_another_case_is_the_vault_on_a_case_blind_disk(self):
        other = self.vault.parent / self.vault.name.upper()
        if not other.exists():
            self.skipTest("this filesystem is case-sensitive")
        proc = self.run_hook("Bash", {"command": f"git worktree add {other}/scratch"}, self.code)
        self.assertRefused(proc)

    def test_a_multi_line_command_is_read_across_its_continuations(self):
        for command in (f"git -C {self.vault} \\\n  worktree add ../w",
                        f"git worktree add -b x \\\n  {self.vault}/scratch \\\n  main"):
            with self.subTest(command=command):
                self.assertRefused(self.run_hook("Bash", {"command": command}, self.code))

    def test_a_script_handed_to_a_shell_is_read(self):
        for command in (f'bash -lc "cd {self.vault} && git worktree add ../w"',
                        f"bash <<'EOF'\ncd {self.vault}\ngit worktree add ../w\nEOF"):
            with self.subTest(command=command):
                self.assertRefused(self.run_hook("Bash", {"command": command}, self.code))


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class ElsewherePasses(_Fixture):

    def test_the_same_calls_in_this_agentm_checkout_pass(self):
        self.assertAllowed(self.run_hook("EnterWorktree", {"name": "x"}, _REPO))
        self.assertAllowed(self.run_hook("Bash", {"command": f"git -C {_REPO} worktree add .claude/worktrees/x"},
                                         self.code))
        self.assertAllowed(self.run_hook(AGENT, {"prompt": "p", "isolation": "worktree"}, _REPO))
        self.assertAllowed(self.run_hook("mcp__ccd_session__spawn_task", {"prompt": "p", "title": "t", "tldr": "t"},
                                         _REPO))

    def test_the_same_calls_in_a_code_repository_pass(self):
        self.assertAllowed(self.run_hook("EnterWorktree", {"name": "x"}, self.code))
        self.assertAllowed(self.run_hook("Bash", {"command": "git worktree add .claude/worktrees/x"}, self.code))

    def test_a_path_that_only_resembles_the_vault_passes(self):
        self.assertTrue(str(self.lookalike).startswith(str(self.vault)))
        self.assertAllowed(self.run_hook("EnterWorktree", {"name": "x"}, self.lookalike))
        self.assertAllowed(self.run_hook("Bash", {"command": f"git -C {self.lookalike} worktree add ../w"},
                                         self.code))
        self.assertAllowed(self.run_hook("mcp__ccd_session__spawn_task", {"prompt": "p", "title": "t", "tldr": "t"},
                                         self.lookalike))

    def test_a_chip_from_a_vault_session_pointed_at_a_code_repository_passes(self):
        proc = self.run_hook("mcp__ccd_session__spawn_task",
                             {"title": "t", "prompt": "p", "tldr": "t", "cwd": str(self.code)}, self.vault)
        self.assertAllowed(proc)

    def test_ordinary_calls_in_the_vault_pass(self):
        self.assertAllowed(self.run_hook("Bash", {"command": "git status && ls"}, self.vault))
        self.assertAllowed(self.run_hook("Bash", {"command": "git worktree list"}, self.vault))
        self.assertAllowed(self.run_hook(AGENT, {"prompt": "p"}, self.vault))
        self.assertAllowed(self.run_hook("Read", {"file_path": "x"}, self.vault))

    def test_a_here_document_that_mentions_the_command_is_text(self):
        command = "cat > notes.md <<'EOF'\ngit worktree add ../x\nEOF\necho done"
        self.assertAllowed(self.run_hook("Bash", {"command": command}, self.vault))

    def test_a_cd_into_the_vault_that_a_subshell_undoes_does_not_count(self):
        for command in (f"(cd {self.vault} && ls) && git worktree add ../w",
                        f"x=$(cd {self.vault} && pwd); git worktree add ../w",
                        f"pushd {self.vault}; ls; popd; git worktree add ../w"):
            with self.subTest(command=command):
                self.assertAllowed(self.run_hook("Bash", {"command": command}, self.code))

    def test_a_commit_message_that_mentions_the_command_is_text(self):
        for command in ("git commit -m $'don\\'t document worktree add'",
                        "git commit -m \"$(cat <<'EOF'\nKeep the worktree add out of the vault\nEOF\n)\""):
            with self.subTest(command=command):
                self.assertAllowed(self.run_hook("Bash", {"command": command}, self.vault))

    def test_an_ordinary_call_in_a_worktree_session_starts_no_python(self):
        # The pre-filter must not match the session's own `.claude/worktrees/` path.
        shim = self.root / "shim"
        shim.mkdir()
        marker = self.root / "python-started"
        (shim / "python3").write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
        (shim / "python3").chmod(0o755)
        env = dict(self.env, PATH=f"{shim}:/usr/bin:/bin")
        session = self.code / ".claude" / "worktrees" / "x"
        session.mkdir(parents=True)
        self.assertAllowed(self.run_hook("Bash", {"command": "ls -la"}, session, env=env))
        self.assertFalse(marker.exists())
        self.run_hook("Bash", {"command": "git worktree add y"}, session, env=env)
        self.assertTrue(marker.exists(), "the pre-filter let nothing through, so the shim proves nothing")

    def test_no_vault_configured_means_allow(self):
        env = dict(self.env)
        env.pop("MEMORY_ROOT")
        self.assertAllowed(self.run_hook("EnterWorktree", {"name": "x"}, self.vault, env=env))

    def test_a_payload_it_cannot_read_means_allow(self):
        for raw in ("", "not json", "[1, 2]", '{"tool_name": "Bash", "tool_input": "worktree"}'):
            with self.subTest(raw=raw):
                proc = subprocess.run(["bash", str(_HOOK_SH)], input=raw, capture_output=True, text=True,
                                      env=self.env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)


class TheCommandReader(unittest.TestCase):
    """The parser on its own, with no git and no vault."""

    def spawns(self, command: str, cwd: str = "/w"):
        # Resolved, the way judge() hands the session's cwd over (`/w` is `D:\w` on Windows).
        return guard.bash_spawns(command, Path(os.path.realpath(cwd)))

    def test_it_follows_cd_and_dash_c_to_where_git_runs(self):
        [s] = self.spawns("cd /a && git -C b worktree add --detach ../c HEAD")
        self.assertEqual((s.cwd, s.dest), (Path(os.path.realpath("/a/b")), Path(os.path.realpath("/a/c"))))

    def test_branch_options_are_not_the_path(self):
        [s] = self.spawns("git worktree add -b feature -f --reason r /p main")
        self.assertEqual(s.dest, Path(os.path.realpath("/p")))

    def test_other_worktree_verbs_are_not_spawns(self):
        for command in ("git worktree list", "git worktree prune", "git worktree remove x",
                        "echo git worktree add x", "git log --grep worktree"):
            with self.subTest(command=command):
                self.assertEqual(self.spawns(command), [])

    def test_a_redirection_is_not_the_path(self):
        [s] = self.spawns("git worktree add 2>/dev/null /p")
        self.assertEqual(s.dest, Path(os.path.realpath("/p")))

    def test_an_unexpandable_variable_is_unknown_not_guessed(self):
        [s] = self.spawns('cd "$SOMEWHERE_UNSET_189" && git worktree add x')
        self.assertIsNone(s.cwd)
        self.assertIsNone(s.dest)

    def test_quoting_the_tokenizer_cannot_follow_fails_open(self):
        # A refusal on a guess would block commands that never touch a worktree.
        self.assertEqual(self.spawns("git worktree add 'x"), [])

    def test_comments_and_here_documents_are_stripped(self):
        text, bodies = guard.strip_comments_and_heredocs("a # c\nb 'x#y' \"#z\" $# <<-E\n\tbody\n\tE\nd")
        mark = guard._HEREDOC_MARK.format(0)
        self.assertEqual(text, f"a \nb 'x#y' \"#z\" $# << {mark}\nd")
        self.assertEqual(bodies, ["\tbody"])

    def test_a_line_continuation_joins_the_lines(self):
        [s] = self.spawns("git -C /a \\\n  worktree add -b x \\\n  /p \\\n  main")
        self.assertEqual((s.cwd, s.dest), (Path(os.path.realpath("/a")), Path(os.path.realpath("/p"))))

    def test_ansi_c_quoting_is_read(self):
        self.assertEqual(self.spawns("git commit -m $'don\\'t document worktree add'"), [])
        [s] = self.spawns("git worktree add $'/p'")
        self.assertEqual(s.dest, Path(os.path.realpath("/p")))

    def test_a_subshell_or_substitution_does_not_move_what_follows(self):
        for command in ("(cd /a && ls) && git worktree add x",
                        "y=$(cd /a && pwd); git worktree add x",
                        "diff <(cd /a && ls) z; git worktree add x",
                        "pushd /a; ls; popd; git worktree add x"):
            with self.subTest(command=command):
                [s] = self.spawns(command)
                self.assertEqual(s.cwd, Path(os.path.realpath("/w")))
        [s] = self.spawns("(cd /a && git worktree add x)")
        self.assertEqual(s.cwd, Path(os.path.realpath("/a")))

    def test_shells_eval_and_prefixes_are_read_through(self):
        for command in ('bash -lc "cd /a && git worktree add x"',
                        "sh -e -c 'cd /a; git worktree add x'",
                        "bash <<'EOF'\ncd /a\ngit worktree add x\nEOF",
                        "eval cd /a '&&' git worktree add x",
                        "timeout 30 env -u FOO nice -n 5 git -C /a worktree add x",
                        "if true; then cd /a; fi; git worktree add x",
                        "{ cd /a; git worktree add x; }"):
            with self.subTest(command=command):
                [s] = self.spawns(command)
                self.assertEqual(s.cwd, Path(os.path.realpath("/a")))

    def test_an_exported_git_dir_carries_to_later_commands(self):
        [s] = self.spawns("export GIT_DIR=/g; git worktree add x")
        self.assertEqual(s.git_dir, Path(os.path.realpath("/g")))
        [s] = self.spawns("GIT_DIR=/g; git worktree add x")  # not exported: git doesn't see it
        self.assertIsNone(s.git_dir)

    def test_a_shell_running_a_script_file_is_not_read(self):
        self.assertEqual(self.spawns("bash script.sh worktree add"), [])

    def test_an_enormous_command_is_allowed_unread(self):
        self.assertEqual(self.spawns("git worktree add x " + "y" * guard.MAX_COMMAND), [])


@unittest.skipIf(shutil.which("pwsh") is None, "pwsh not installed")
class ThePwshTwin(_Fixture):

    def test_it_refuses_in_the_vault_and_passes_elsewhere(self):
        self.assertRefused(self.run_hook("EnterWorktree", {"name": "x"}, self.vault, hook=_HOOK_PS1))
        self.assertAllowed(self.run_hook("EnterWorktree", {"name": "x"}, self.code, hook=_HOOK_PS1))


class TheRegistration(unittest.TestCase):

    def test_both_fragments_register_one_pretooluse_hook_on_the_four_tools(self):
        import re
        for name, runner in (("settings-fragment-bash.json", "vault-worktree-guard.sh"),
                             ("settings-fragment-pwsh.json", "vault-worktree-guard.ps1")):
            with self.subTest(fragment=name):
                frag = json.loads((_HOOK_DIR / name).read_text(encoding="utf-8"))
                [entry] = frag["hooks"]["PreToolUse"]
                matcher = re.compile(entry["matcher"])
                for tool in ("Bash", "EnterWorktree", *guard.AGENT_TOOLS, "mcp__ccd_session__spawn_task"):
                    self.assertTrue(matcher.search(tool), tool)
                for tool in ("Read", "Edit", "TaskStop", "mcp__ccd_session__spawn_task_x"):
                    self.assertFalse(matcher.search(tool), tool)
                [hook] = entry["hooks"]
                self.assertIn(runner, hook["command"])


if __name__ == "__main__":
    unittest.main()
