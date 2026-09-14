#!/usr/bin/env python3
"""Tests for scripts/migrate/root_casing.py (agentm-vault plan 08).

A scratch vault under git with the four Title Case roots, the settings that
name them and an ignore file. The two-step through a temporary name is what
moves the index with the tree; on a case-sensitive disk it is merely two
renames, on a case-insensitive one it is the only rename git sees. The
writers-quiet check, the pause and the config readers are injected, so no
test touches launchd, the config file or the clock.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("root_casing", _HERE / "migrate" / "root_casing.py")
assert _SPEC and _SPEC.loader
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)  # type: ignore[union-attr]

OLD = [old for old, _new in mig.RENAMES]  # root-casing: the names the fixture builds, to rename
NEW = [new for _old, new in mig.RENAMES]

DAILY = '{\n  "folder": "Calendar",\n  "format": "YYYY-MM-DD",\n  "template": "Calendar/_daily-template",\n  "autorun": false\n}\n'  # root-casing: the settings before the rename
APP = '{\n  "userIgnoreFilters": [\n    "Agent/desk/scratch"\n  ],\n  "alwaysUpdateLinks": true\n}\n'  # root-casing: the settings before the rename
BOOKMARKS = '{\n  "items": [\n    {"type": "file", "path": "Home/Important Docs/Web.md"},\n    {"type": "file", "path": "Personal/Home/Important Docs/Web.md", "title": "Web"}\n  ]\n}\n'  # root-casing: the settings before the rename
GITIGNORE = ".obsidian/workspace*\n.DS_Store\nAgent/_meta/vec-index.db\nAgent/.heat.json\n"  # root-casing: the ignore file before the rename

GOOD_CONFIG = {"plugins.obsidian-vault.memory_root": "agent",
               "daemon.spaces": {"memory": "agent/memory", "projects": "projects"}}


def _git(vault: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True)
    return r.stdout


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.vault, self.state = base / "Vault", base / "state"
        self.out = self.state / mig.STAGE
        for old in OLD:
            for i in range(3):
                p = self.vault / old / "memory" / f"n-{i}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"---\ntitle: {old} {i}\n---\n\nBody {i}.\n", encoding="utf-8")
        (self.vault / "standards").mkdir()
        (self.vault / "standards" / "storage-rules.md").write_text("rules\n", encoding="utf-8")
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "daily-notes.json").write_text(DAILY, encoding="utf-8")
        (self.vault / ".obsidian" / "app.json").write_text(APP, encoding="utf-8")
        (self.vault / ".obsidian" / "bookmarks.json").write_text(BOOKMARKS, encoding="utf-8")
        (self.vault / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        _git(self.vault, "init", "-q")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "fixture")
        self.exports = base / "project.json"
        self.exports.write_text(json.dumps({"env": {"MEMORY_VAULT_PATH": str(self.vault / "agent")}}), encoding="utf-8")

    def plan(self):
        plan = mig.build_plan(self.vault)
        plan["run_id"] = "run-1"
        return plan

    def apply(self, plan, count=None, writers=lambda: [], run=None):
        return mig.apply(self.vault, plan, plan["counts"]["renames"] if count is None else count, self.out,
                         writers=writers, sleep=lambda s: None, run=run)

    def finish(self, plan, config=GOOD_CONFIG, resolver=None):
        reader = lambda key: config.get(key)  # noqa: E731
        return mig.finish(self.vault, plan, self.out, config_reader=reader,
                          exports={"the exports": self.exports},
                          resolver=resolver or (lambda v: (str(v), str(v / "agent"))))

    def tracked(self, name):
        return mig.tracked_count(self.vault, name)


class DryRun(Fixture):
    def test_plans_the_four_renames_with_their_counts(self):
        plan = self.plan()
        self.assertEqual([r["state"] for r in plan["renames"]], ["pending"] * 4)
        self.assertEqual([r["tracked"] for r in plan["renames"]], [3, 3, 3, 3])
        self.assertEqual(plan["counts"], {"renames": 4, "tracked": 12})
        self.assertTrue(all(plan["settings"][rel]["changes"] for rel in mig.SETTINGS_REL))

    def test_the_manifest_gives_each_rename_and_its_reverse(self):
        lines = mig.manifest(self.plan())
        self.assertEqual(len(lines), 4 + 4)
        self.assertIn(f"git -C \"{self.vault}\" mv {OLD[0]} .{NEW[0]}-tmp && git -C \"{self.vault}\" mv .{NEW[0]}-tmp {NEW[0]}", lines[0])
        self.assertIn(f"Reverse: `git -C \"{self.vault}\" mv {NEW[0]} .{NEW[0]}-tmp && git -C \"{self.vault}\" mv .{NEW[0]}-tmp {OLD[0]}`", lines[0])

    def test_a_temporary_name_or_a_twin_refuses(self):
        (self.vault / f".{NEW[1]}-tmp").mkdir()
        with self.assertRaises(mig.Refused):
            mig.build_plan(self.vault)
        (self.vault / f".{NEW[1]}-tmp").rmdir()
        (self.vault / NEW[2]).mkdir(exist_ok=True)
        if NEW[2] in mig.listing(self.vault) and OLD[2] in mig.listing(self.vault):
            with self.assertRaises(mig.Refused):
                mig.build_plan(self.vault)

    def test_a_vault_outside_git_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / OLD[0]).mkdir()
            with self.assertRaises(mig.Refused):
                mig.build_plan(Path(td))


class Apply(Fixture):
    def test_renames_move_the_index_with_the_tree(self):
        plan = self.plan()
        journal = self.apply(plan)
        self.assertTrue(journal["landed"], journal)
        names = mig.listing(self.vault)
        for old, new in zip(OLD, NEW):
            self.assertIn(new, names)
            self.assertNotIn(old, names)
            self.assertEqual(self.tracked(new), 3)
            self.assertEqual(self.tracked(old), 0)
        status = _git(self.vault, "status", "--porcelain")
        self.assertIn(f"R  {OLD[0]}/memory/n-0.md -> {NEW[0]}/memory/n-0.md", status)
        self.assertEqual(journal["returned"], [])
        self.assertTrue((self.out / "journal-run-1.json").is_file())

    def test_the_settings_are_rewritten_and_paths_inside_a_space_keep_their_case(self):
        self.apply(self.plan())
        daily = json.loads((self.vault / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8"))
        self.assertEqual(daily["folder"], NEW[1])
        self.assertEqual(daily["template"], f"{NEW[1]}/_daily-template")
        app = json.loads((self.vault / ".obsidian" / "app.json").read_text(encoding="utf-8"))
        self.assertEqual(app["userIgnoreFilters"], [f"{NEW[0]}/desk/scratch"])
        marks = json.loads((self.vault / ".obsidian" / "bookmarks.json").read_text(encoding="utf-8"))
        self.assertEqual(marks["items"][1]["path"], f"{NEW[2]}/Home/Important Docs/Web.md")
        self.assertEqual(marks["items"][0]["path"], "Home/Important Docs/Web.md")
        self.assertIn(f"{NEW[0]}/.heat.json", (self.vault / ".gitignore").read_text(encoding="utf-8"))
        # The settings are left unstaged: the hand commit holds the renames alone.
        self.assertNotIn(".obsidian", _git(self.vault, "diff", "--cached", "--name-only"))
        self.assertIn(".obsidian/app.json", _git(self.vault, "diff", "--name-only"))

    def test_refuses_a_changed_listing_a_wrong_count_a_live_writer_and_a_staged_index(self):
        plan = self.plan()
        with self.assertRaises(mig.Refused):
            self.apply(plan, count=3)
        with self.assertRaises(mig.Refused):
            self.apply(plan, writers=lambda: ["Obsidian is running"])
        (self.vault / "standards" / "new.md").write_text("x\n", encoding="utf-8")
        _git(self.vault, "add", "standards/new.md")
        with self.assertRaises(mig.Refused):
            self.apply(plan)
        _git(self.vault, "reset", "-q")
        (self.vault / "Extra").mkdir()
        with self.assertRaises(mig.Refused):
            self.apply(plan)
        self.assertEqual(sorted(n for n in mig.listing(self.vault) if n in OLD), sorted(OLD))

    def test_a_reflect_hook_that_starts_between_roots_stops_the_run(self):
        plan = self.plan()
        calls = {"n": 0}

        def writers():
            calls["n"] += 1
            return ["a memory-reflect hook is running"] if calls["n"] == 3 else []
        journal = self.apply(plan, writers=writers)
        self.assertFalse(journal["landed"])
        self.assertEqual([r["new"] for r in journal["renames"] if r.get("landed")], [NEW[0]])
        self.assertIn("skipped", journal["renames"][1])
        self.assertIn(OLD[1], mig.listing(self.vault))

    def test_a_second_dry_run_plans_nothing(self):
        self.apply(self.plan())
        plan = mig.build_plan(self.vault)
        self.assertEqual([r["state"] for r in plan["renames"]], ["done"] * 4)
        self.assertEqual(plan["counts"]["renames"], 0)
        self.assertFalse(any(s["changes"] for s in plan["settings"].values()))


class Finish(Fixture):
    def test_writes_the_marker_only_when_everything_holds(self):
        plan = self.plan()
        self.apply(plan)
        bad = {"plugins.obsidian-vault.memory_root": OLD[0], "daemon.spaces": {"memory": f"{OLD[0]}/memory", "projects": OLD[3]}}
        problems = self.finish(plan, config=bad)
        self.assertEqual(len(problems), 3, problems)
        self.assertFalse((self.vault / mig.MARKER_REL).exists())
        self.exports.write_text(json.dumps({"env": {"MEMORY_VAULT_PATH": str(self.vault / OLD[0])}}), encoding="utf-8")
        problems = self.finish(plan)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("must end in /agent", problems[0])
        self.exports.write_text(json.dumps({"env": {"MEMORY_VAULT_PATH": str(self.vault / "agent")}}), encoding="utf-8")
        problems = self.finish(plan, resolver=lambda v: (str(v / "agent"), str(v / "agent")))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("with no override", problems[0])
        self.assertEqual(self.finish(plan), [])
        marker = self.vault / mig.MARKER_REL
        self.assertTrue(marker.is_file())
        self.assertIn("run run-1", marker.read_text(encoding="utf-8"))

    def test_a_returned_title_case_directory_fails_the_finish(self):
        """A writer that composed the old path, or a sync that put the old
        name back: on a case-insensitive disk that is the lowercase directory
        renamed, on a case-sensitive one a twin beside it. Either way the
        listing shows the old name, and the finish refuses."""
        plan = self.plan()
        self.apply(plan)
        (self.vault / NEW[0]).rename(self.vault / OLD[0])
        problems = self.finish(plan)
        self.assertTrue(any(f"{OLD[0]}/ exists" in p for p in problems), problems)

    def test_needs_an_applied_run(self):
        with self.assertRaises(mig.Refused):
            self.finish(self.plan())


class Revert(Fixture):
    def test_moves_every_root_back_and_restores_the_settings(self):
        plan = self.plan()
        self.apply(plan)
        self.assertEqual(self.finish(plan), [])
        mig.revert(self.vault, "run-1", self.out, sleep=lambda s: None)
        names = mig.listing(self.vault)
        for old, new in zip(OLD, NEW):
            self.assertIn(old, names)
            self.assertNotIn(new, names)
            self.assertEqual(self.tracked(old), 3)
        self.assertEqual((self.vault / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8"), DAILY)
        self.assertEqual((self.vault / ".gitignore").read_text(encoding="utf-8"), GITIGNORE)
        self.assertFalse((self.vault / mig.MARKER_REL).exists())

    def test_refuses_when_a_settings_file_changed_since(self):
        plan = self.plan()
        self.apply(plan)
        p = self.vault / ".obsidian" / "app.json"
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(mig.Refused):
            mig.revert(self.vault, "run-1", self.out, sleep=lambda s: None)
        self.assertIn(NEW[0], mig.listing(self.vault))

    def test_refuses_when_a_title_case_directory_is_back(self):
        plan = self.plan()
        self.apply(plan)
        (self.vault / NEW[2]).rename(self.vault / OLD[2])
        with self.assertRaises(mig.Refused):
            mig.revert(self.vault, "run-1", self.out, sleep=lambda s: None)
        # Nothing else moved back: the refusal restores nothing.
        self.assertIn(NEW[0], mig.listing(self.vault))


if __name__ == "__main__":
    unittest.main()
