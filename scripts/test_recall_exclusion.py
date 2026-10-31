#!/usr/bin/env python3
"""A project's `recall_excludes:` keeps another project's notes out of its sessions.

The rule is a wall on every door a session has, so these tests go through the
doors: the real hook scripts with a real payload, the
in-process engine and a stand-in `agentmd` that answers the way the daemon does,
`recall.py query`, and deep search. Each door is checked twice, because a wall
that also drops everyone else's notes would pass a test that only looks for the
excluded path.

The folder the session works in is the input, so a worktree path is a case
rather than an afterthought. A rule that cannot be read has to withhold and say
so, which is the one thing here no other reader in this repo does.
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import deep_search  # noqa: E402
import recall  # noqa: E402
import recall_exclusion  # noqa: E402
from recall_exclusion import Exclusion  # noqa: E402

PROMPT_HOOK = _REPO / "harness/hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.sh"
START_HOOK = _REPO / "harness/hooks/memory-recall-session-start/memory-recall-session-start.sh"

PROMPT = "how does the zoning floodplain simulation work"
PORT_NOTE = "projects/port/research/zoning-floodplain.md"
OWN_NOTE = "projects/clean/research/zoning-floodplain.md"
KEPT_NOTE = "agent/memory/semantic/zoning-floodplain.md"
STAMPED_NOTE = "agent/memory/semantic/zoning-floodplain-card.md"
COMPLETED_NOTE = "projects/completed/port/research/zoning-floodplain-old.md"
BODY = "zoning floodplain simulation notes"


class _Vault(unittest.TestCase):
    """`clean` excludes `port`; `other` has no rule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.vault = self.root / "Vault"
        self.memory = self.vault / "agent"
        self.code = self.root / "code" / "clean"
        self.other = self.root / "code" / "other"
        self.worktree = self.code / ".claude" / "worktrees" / "slot"
        # `.obsidian/` is what marks `agent/` as a memory root inside a vault.
        for d in (self.worktree, self.other, self.root / "code" / "port", self.vault / ".obsidian"):
            d.mkdir(parents=True)
        self.write_rule("recall_excludes:\n  - port\n")
        self.write("projects/port/project.yaml",
                   f"slug: port\ncode_paths:\n  - {self.root}/code/port\n")
        self.write("projects/other/project.yaml", f"slug: other\ncode_paths:\n  - {self.other}\n")
        self.write(PORT_NOTE, f"---\nkind: reference\ntitle: t\n---\n{BODY} port-folder\n")
        self.write(OWN_NOTE, f"---\nkind: reference\ntitle: t\n---\n{BODY} own-folder\n")
        self.write(KEPT_NOTE, f"---\nkind: reference\ntitle: t\n---\n{BODY} shared-memory\n")
        self.write(COMPLETED_NOTE, f"---\nkind: reference\ntitle: t\n---\n{BODY} port-completed\n")
        self.write(STAMPED_NOTE, f"---\nkind: reference\ntitle: t\nproject: port\n---\n{BODY} port-stamped\n")

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_rule(self, rule):
        self.write("projects/clean/project.yaml",
                   f"slug: clean\ncode_paths:\n  - {self.code}\n{rule}")

    def exclusion(self, directory):
        return recall_exclusion.for_session(directory, self.memory)


class TheRuleFollowsTheFolder(_Vault):
    def test_a_session_of_the_project_is_under_the_rule(self):
        for directory in (self.code, self.worktree, self.code / "nested",
                          self.vault / "projects" / "clean",
                          self.vault / "projects" / "clean" / "tasks" / "a"):
            with self.subTest(directory=directory):
                self.assertEqual(self.exclusion(directory), Exclusion(slugs=("port",)))

    def test_a_worktree_outside_the_clone_is_known_by_its_git_file(self):
        slot = self.root / "elsewhere" / "slot"
        (slot / "sub").mkdir(parents=True)
        (slot / ".git").write_text(f"gitdir: {self.code}/.git/worktrees/slot\n", encoding="utf-8")
        self.assertEqual(self.exclusion(slot / "sub"), Exclusion(slugs=("port",)))

    def test_a_vault_worktree_of_the_project_folder_is_under_the_rule(self):
        slot = self.vault / ".claude" / "worktrees" / "slot"
        (slot / "projects" / "clean").mkdir(parents=True)
        (slot / ".git").write_text(f"gitdir: {self.vault}/.git/worktrees/slot\n", encoding="utf-8")
        self.assertEqual(self.exclusion(slot / "projects" / "clean"), Exclusion(slugs=("port",)))

    def test_a_submodule_git_file_says_nothing_about_a_project(self):
        sub = self.root / "elsewhere" / "sub"
        sub.mkdir(parents=True)
        (sub / ".git").write_text(f"gitdir: {self.code}/.git/modules/sub\n", encoding="utf-8")
        self.assertEqual(self.exclusion(sub), recall_exclusion.NONE)

    def test_nobody_else_is_under_it(self):
        for directory in (self.vault / "projects" / "port", self.root / "code" / "port",
                          self.other, self.root, self.vault / "agent"):
            with self.subTest(directory=directory):
                self.assertEqual(self.exclusion(directory), recall_exclusion.NONE)

    def test_a_rule_can_name_several_projects(self):
        self.write_rule("recall_excludes: [port, other]\n")
        self.assertEqual(self.exclusion(self.code).slugs, ("port", "other"))


class ARuleThatCannotBeReadFailsClosed(_Vault):
    def test_a_rule_that_is_not_a_list(self):
        self.write_rule("recall_excludes: port\n")
        self.assertIn("recall_excludes", self.exclusion(self.code).problem)

    def test_a_rule_naming_no_project(self):
        self.write_rule("recall_excludes:\n  - prot\n")
        self.assertIn("prot", self.exclusion(self.worktree).problem)
        self.assertIn("prot", self.exclusion(self.vault / "projects" / "clean").problem)

    def test_a_file_that_will_not_read(self):
        path = self.vault / "projects/clean/project.yaml"
        path.unlink()
        path.mkdir()  # a read fails whoever runs this
        self.assertIn("clean", self.exclusion(self.code).problem)
        self.assertIn("clean", self.exclusion(self.vault / "projects" / "clean").problem)
        # A project that still reads is known, so its session is not held up.
        self.assertEqual(self.exclusion(self.other), recall_exclusion.NONE)

    def test_a_folder_nobody_can_name_is_not_a_clear_session(self):
        self.assertIn("unknown", recall_exclusion.for_session(None, self.memory).problem)

    def test_a_reader_that_breaks_is_a_problem_rather_than_a_clear_session(self):
        original = recall_exclusion._read_rules
        recall_exclusion._read_rules = lambda _root: 1 / 0
        self.addCleanup(setattr, recall_exclusion, "_read_rules", original)
        self.assertIn("ZeroDivisionError", self.exclusion(self.code).problem)


class Hides(unittest.TestCase):
    def test_paths_and_stamps(self):
        rule = Exclusion(slugs=("port",))
        for rel in ("projects/port/a.md", "../projects/port/desk/b/c.md", "agent/projects/port/a.md",
                    "projects/completed/port/a.md", "projects/Port/a.md"):
            with self.subTest(rel=rel):
                self.assertTrue(rule.hides(rel))
        for rel in ("projects/port-two/a.md", "projects/clean/research/port.md", "projects/port.md",
                    "projects/completed/port.md", "memory/semantic/port.md"):
            with self.subTest(rel=rel):
                self.assertFalse(rule.hides(rel))
        self.assertTrue(rule.hides("memory/a.md", "port"))
        self.assertTrue(rule.hides("memory/a.md", '"port"'))
        self.assertFalse(rule.hides("memory/a.md", "clean"))
        self.assertFalse(recall_exclusion.NONE.hides("projects/port/a.md", "port"))


def _fake_agentmd(bin_dir: Path, rows, *, refuse_cwd=False) -> None:
    """An `agentmd` that answers like the daemon: vault-relative paths and the
    project head, and logs its arguments. A shell script, because the hook gives
    the daemon 250ms and an interpreter's start-up is most of that."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    answer = bin_dir / "answer.json"
    answer.write_text(json.dumps({"results": rows, "always_load_hidden": 0, "matched": len(rows)}),
                      encoding="utf-8")
    refuse = 'case " $* " in *" -cwd "*) echo "flag provided but not defined: -cwd" >&2; exit 2;; esac\n'
    tool = bin_dir / "agentmd"
    tool.write_text(f'''#!/bin/sh
echo "$*" >> "{bin_dir}/argv.log"
{refuse if refuse_cwd else ""}cat "{answer}"
''', encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)


@unittest.skipIf(os.name == "nt", "the bash hooks; PowerShell's forward the same payload")
class _Hooks(_Vault):
    """The real hook scripts, in a scratch environment with a scratch prefix."""

    def setUp(self):
        super().setUp()
        self.home = self.root / "home"
        self.prefix = self.home / ".claude"
        (self.prefix / "skills" / "memory").mkdir(parents=True)
        (self.prefix / "skills" / "memory" / "scripts").symlink_to(_SCRIPTS)
        self.state = self.root / "state"
        self.state.mkdir()
        self.bin = self.root / "bin"
        _fake_agentmd(self.bin, [
            {"path": PORT_NOTE, "score": 9.0},
            {"path": COMPLETED_NOTE, "score": 8.0},
            {"path": STAMPED_NOTE, "score": 7.0, "project": "port"},
            {"path": OWN_NOTE, "score": 6.0},
            {"path": KEPT_NOTE, "score": 5.0},
        ])

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("MEMORY_VAULT_PATH", "AGENTM_HOST", "AGENTMD_SCOPE")}
        env.update(HOME=str(self.home), MEMORY_ROOT=str(self.memory),
                   AGENTM_INSTALL_PREFIX=str(self.prefix), AGENTM_STATE_DIR=str(self.state),
                   XDG_STATE_HOME=str(self.state), PYTHONDONTWRITEBYTECODE="1",
                   RECALL_BUDGET_MS="5000",
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        return env

    def run_hook(self, script, directory, payload):
        return subprocess.run(["bash", str(script)], cwd=directory, env=self.env(),
                              input=json.dumps(payload), capture_output=True, text=True, timeout=60)

    def prompt(self, directory, *, payload_cwd=True):
        payload = {"prompt": PROMPT}
        if payload_cwd:
            payload["cwd"] = str(directory)
        return self.run_hook(PROMPT_HOOK, directory, payload)

    def start(self, directory):
        return self.run_hook(START_HOOK, directory, {"cwd": str(directory), "session_id": "s"})


class ThePromptHook(_Hooks):
    def assertWalled(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        for held_back in ("port-folder", "port-completed", "port-stamped"):
            self.assertNotIn(held_back, r.stdout)
        self.assertIn("own-folder", r.stdout)
        self.assertIn("shared-memory", r.stdout)
        self.assertIn("recall_excludes: port", r.stderr)

    def test_a_worktree_of_the_project_gets_no_hit_and_keeps_the_rest(self):
        self.assertWalled(self.prompt(self.worktree))

    def test_the_clone(self):
        self.assertWalled(self.prompt(self.code))

    def test_the_project_folder_in_the_vault(self):
        self.assertWalled(self.prompt(self.vault / "projects" / "clean"))

    def test_a_payload_with_no_cwd_falls_back_to_the_hooks_own_folder(self):
        self.assertWalled(self.prompt(self.worktree, payload_cwd=False))

    def test_a_neutral_folder_sees_no_change(self):
        for directory in (self.other, self.root, self.vault / "projects" / "port"):
            with self.subTest(directory=directory):
                r = self.prompt(directory)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("port-folder", r.stdout)
                self.assertNotIn("recall_excludes", r.stderr)

    def test_the_payloads_folder_decides_not_the_process_folder(self):
        """On Antigravity the hook runs wherever the host ran it, and the
        workspace arrives in the payload."""
        r = self.run_hook(PROMPT_HOOK, self.other, {"prompt": PROMPT, "cwd": str(self.worktree)})
        self.assertWalled(r)

    def test_only_a_session_under_a_rule_sends_the_flag(self):
        self.prompt(self.other)
        self.assertNotIn("-cwd", (self.bin / "argv.log").read_text(encoding="utf-8"))
        self.prompt(self.worktree)
        self.assertIn(str(self.worktree), (self.bin / "argv.log").read_text(encoding="utf-8"))

    def test_an_unreadable_rule_injects_nothing_and_says_so(self):
        self.write_rule("recall_excludes: port\n")
        r = self.prompt(self.worktree)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("recall exclusions could not be read", r.stderr)
        self.assertFalse((self.bin / "argv.log").exists(), "the search ran anyway")


class TheSessionStartHook(_Hooks):
    """The always-load tier is the operator's rules, so a stamped pen entry is
    the one way a project's note can arrive at session start."""

    def setUp(self):
        super().setUp()
        self.write("standards/user-preferences.md", "---\ntitle: p\n---\nstanding preference\n")
        self.write("agent/memory/_always-load/port-pin.md",
                   "---\ntitle: pin\nproject: port\n---\npinned port detail\n")

    def test_a_worktree_of_the_project_does_not_load_the_stamped_entry(self):
        r = self.start(self.worktree)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("standing preference", r.stdout)
        self.assertNotIn("pinned port detail", r.stdout)

    def test_the_payloads_folder_decides_not_the_process_folder(self):
        r = self.run_hook(START_HOOK, self.other, {"cwd": str(self.worktree), "session_id": "s"})
        self.assertIn("standing preference", r.stdout)
        self.assertNotIn("pinned port detail", r.stdout)

    def test_a_neutral_folder_sees_no_change(self):
        r = self.start(self.other)
        self.assertIn("pinned port detail", r.stdout)

    def test_the_excluded_project_itself_sees_no_change(self):
        r = self.start(self.root / "code" / "port")
        self.assertIn("pinned port detail", r.stdout)

    def test_an_unreadable_rule_keeps_the_standing_rules_and_withholds_the_rest(self):
        self.write_rule("recall_excludes: port\n")
        r = self.start(self.worktree)
        self.assertIn("standing preference", r.stdout)
        self.assertNotIn("pinned port detail", r.stdout)
        self.assertIn("recall exclusions could not be read", r.stderr)


class TheInProcessEngine(_Vault):
    def ranked(self, exclusion=recall_exclusion.NONE):
        hits = recall.query(vault=self.memory, query_text="zoning floodplain", k=10,
                            exclusion=exclusion, stderr=io.StringIO())
        return {h["path"] for h in hits}

    def test_the_rule_walls_the_walk_and_leaves_the_slots_to_real_hits(self):
        open_paths = self.ranked()
        self.assertTrue(any("projects/port/" in p for p in open_paths), open_paths)
        walled = self.ranked(Exclusion(slugs=("port",)))
        self.assertFalse([p for p in walled if "/port/" in p or p.endswith("-card.md")], walled)
        self.assertTrue([p for p in walled if p.endswith("zoning-floodplain.md")], walled)

    def test_the_daemon_fallback_prompt_path_holds_the_rule(self):
        out = io.StringIO()
        recall.DAEMON_BIN, saved = "agentmd-not-on-this-path", recall.DAEMON_BIN
        self.addCleanup(setattr, recall, "DAEMON_BIN", saved)
        recall.prompt_submit(vault=self.memory, prompt="zoning floodplain simulation", budget_ms=5000,
                             exclusion=Exclusion(slugs=("port",)), stdout=out, stderr=io.StringIO())
        self.assertIn("shared-memory", out.getvalue())
        self.assertNotIn("port-folder", out.getvalue())


class ABinaryOlderThanTheFlag(_Hooks):
    """Called directly, with a budget no machine misses: the hook's own 250ms is
    what the daemon gets, and two process starts inside it is a race on a loaded
    runner. The rule's holding is the point here, not the budget."""

    def test_it_is_asked_again_and_the_rule_still_holds(self):
        _fake_agentmd(self.bin, [{"path": PORT_NOTE, "score": 9.0}, {"path": KEPT_NOTE, "score": 5.0}],
                      refuse_cwd=True)
        saved, recall.DAEMON_BIN = recall.DAEMON_BIN, str(self.bin / "agentmd")
        self.addCleanup(setattr, recall, "DAEMON_BIN", saved)
        status: dict = {}
        hits = recall._daemon_search(vault=self.memory, query_text=PROMPT, budget_ms=30_000, status=status,
                                     exclusion=Exclusion(slugs=("port",)), session_cwd=str(self.worktree))
        self.assertEqual([h["path"] for h in hits], ["memory/semantic/zoning-floodplain.md"], status)
        calls = (self.bin / "argv.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(["-cwd" in c.split() for c in calls], [True, False])


class TheSearchASessionRuns(_Hooks):
    def query(self, directory, *extra):
        return subprocess.run([sys.executable, str(_SCRIPTS / "recall.py"), "query",
                               "zoning floodplain", "-k", "10", *extra],
                              cwd=directory, env=self.env(), capture_output=True, text=True, timeout=60)

    def test_memory_search_from_a_worktree(self):
        walled, neutral = self.query(self.worktree), self.query(self.other)
        self.assertEqual(walled.returncode, 0, walled.stderr)
        self.assertNotIn("projects/port", walled.stdout)
        self.assertIn("projects/port", neutral.stdout)

    def test_memory_search_heads_from_a_worktree(self):
        walled = self.query(self.worktree, "--heads")
        self.assertNotIn("projects/port", walled.stdout)
        self.assertIn("zoning-floodplain", walled.stdout)

    def test_memory_search_refuses_an_unreadable_rule(self):
        self.write_rule("recall_excludes: port\n")
        r = self.query(self.worktree)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("recall exclusions could not be read", r.stderr)


class DeepSearch(_Hooks):
    def setUp(self):
        super().setUp()
        self.write("agent/archive/memory/semantic/port-old.md",
                   "---\ntitle: old\nproject: port\n---\nzoning floodplain\n")
        self.write("agent/archive/memory/semantic/kept-old.md", "---\ntitle: kept\n---\nzoning floodplain\n")
        self.write("agent/diagnostics/migrations/purge/1/manifest.json", json.dumps({"rows": [
            {"rel": "../projects/port/gone.md", "title": "zoning floodplain"},
            {"rel": "memory/semantic/gone.md", "title": "zoning floodplain"}]}))

    def deep(self, directory):
        return subprocess.run([sys.executable, str(_SCRIPTS / "deep_search.py"), "zoning floodplain",
                               "--vault", str(self.memory), "--json"],
                              cwd=directory, env=self.env(), capture_output=True, text=True, timeout=60)

    def test_a_worktree_gets_the_rest(self):
        report = json.loads(self.deep(self.worktree).stdout)
        self.assertEqual([h["rel"] for h in report["archived"]], ["archive/memory/semantic/kept-old.md"])
        self.assertEqual([r["rel"] for r in report["deleted"]], ["memory/semantic/gone.md"])

    def test_a_neutral_folder_sees_no_change(self):
        report = json.loads(self.deep(self.other).stdout)
        self.assertEqual(len(report["archived"]), 2)
        self.assertEqual(len(report["deleted"]), 2)

    def test_an_unreadable_rule_refuses(self):
        self.write_rule("recall_excludes: port\n")
        r = self.deep(self.worktree)
        self.assertEqual(r.returncode, 1)
        self.assertIn("recall exclusions could not be read", r.stderr)

    def test_search_defaults_to_no_rule(self):
        self.assertEqual(len(deep_search.search(self.memory, self.vault, "zoning floodplain")["archived"]), 2)


if __name__ == "__main__":
    unittest.main()
