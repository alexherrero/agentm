#!/usr/bin/env python3
"""The maps and root notes migration (agentm-vault plan 07), on a fixture vault
under git: the dry run plans without writing, the apply refuses a moved vault or
a wrong count and lands the plan through the revert log, the revert brings every
byte back, and the marker waits for every post-condition."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT), str(_HERE / "migrate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import maps_and_root_notes as mig  # noqa: E402
import maps_shape as ms  # noqa: E402
import storage_rules  # noqa: E402

RULES = storage_rules.StorageRules({
    "memory_types": ["workflow", "fix", "convention"],
    "record_kinds": ["moc", "dir-index", "session-trace", "reference"],
    "thresholds": {"moc_min_members": 5},
    "facets": ["meetings", "correspondence", "docs", "diary"],
})

INDEX = ("---\ntitle: index\nkind: reference\nstatus: active\n---\n\n# Vault\n\n"
         "| space | what it holds | authority |\n|---|---|---|\n| `Agent/` | memory | FRIDAY writes freely |\n\n"
         "Loose at the root: [[Ideas]], [[Filing]], and this file. FRIDAY's own entry\npoint is [[Agent/Home]].\n\n"
         "- [[Filing]] — the declared address space.\n")
DRAFT = ("---\ntitle: index\nkind: reference\nstatus: active\n---\n\n# Vault\n\n"
         "| space | what it holds | authority |\n|---|---|---|\n| `Agent/` | memory | FRIDAY writes freely |\n\n"
         "Loose at the root: [[Ideas]] and this file. FRIDAY's own entry point is [[moc-root]].\n")
FILES = {
    ".obsidian/daily-notes.json": '{"folder": "Calendar"}\n',
    "index.md": INDEX,
    "Filing.md": "# Filing\n\n| space | authority |\n|---|---|\n| root files | yours: this file, [[index]] |\n",
    "Agent/Home.md": "# Home\n\n- [[Projects/agentm/_index|agentm]]\n",
    "Agent/memory/mocs/_index.md": "---\ntitle: mocs\nkind: dir-index\nstatus: active\n---\n",
    "Agent/memory/mocs/workflow.md": "---\nkind: moc\n---\n\n# workflow\n\n[[Home]]\n",
    "Agent/memory/mocs/workflow-2.md": "---\nkind: moc\n---\n\n# workflow\n\n[[Home]]\n",
    "Agent/memory/mocs/fix.md": "---\nkind: moc\n---\n\n# fix\n\n[[Home]]\n",
    "Agent/memory/mocs/needs-review.md": "---\nkind: moc\n---\n\n# Needs review\n\n[[Home]]\n",
    "Agent/memory/semantic/blog-author.md": "---\ntitle: blog\ntype: convention\nstatus: active\n---\n\n- [[Home]] — vault map.\n",
    "Agent/memory/episodic/trace.md": ("---\nkind: session-trace\nstatus: active\n---\n\n"
                                       "- **The `[[Home]]` trap:** a bare link would open the personal note.\n"
                                       "- [[workflow-2]]\n- [[fix]]\n"),
    "Projects/agentm/decisions/memory-os-architecture-scan.md": "| 6 LLM Wiki | our wiki + [[Home]] MOC | core |\n\n- [[Home]] — vault map.\n",
    "Projects/agentm/decisions/liteparse-doc-ingestion-candidate.md": "- [[Home]] — vault map.\n",
    "Projects/agentm/decisions/autonomous-write-contract.md": "- [[silent-source-influences]] · [[Home]].\n",
    "Projects/agentm/pattern/corpus-screening-funnel.md": "- [[Home]] — vault map.\n",
    "Projects/crickets/decisions/agentskills-io-interop.md": "- [[projects/crickets/_index|crickets]] · [[Home]].\n",
    "Projects/index.md": "all of it as one revertible commit. See [[Filing]].\n",
    "Projects/agentm/_harness/progress-x.md": "A record keeps [[Home]] and [[Filing]].\n",
    "Calendar/2026-08-10.md": "![[Agent/desk/briefs/20260810-digest-daily]]\n\n## Today\n\nStage 1 ran today.\n",
    "Calendar/_daily-template.md": "![[Agent/desk/briefs/{{date:YYYYMMDD}}-digest-daily]]\n",
    "Calendar/2026/.gitkeep": "",
}


def _git(vault: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.vault, self.logs, self.locks, self.state = base / "vault", base / "logs", base / "locks", base / "state"
        self.root = self.vault / "Agent"
        for rel, text in FILES.items():
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for i in range(7):
            (self.root / "memory" / "procedural" / f"w-{i}.md").parent.mkdir(parents=True, exist_ok=True)
            (self.root / "memory" / "procedural" / f"w-{i}.md").write_text(
                f"---\ntitle: w{i}\ntype: workflow\nstatus: active\n---\n\nStep {i}.\n", encoding="utf-8")
        for i in range(4):
            (self.root / "memory" / "semantic" / f"f-{i}.md").write_text(
                f"---\ntitle: f{i}\ntype: fix\nstatus: active\n---\n\nFix {i}.\n", encoding="utf-8")
        _git(self.vault, "init", "-q")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "fixture")
        self.draft = self.state / "index-draft.md"
        self.draft.parent.mkdir(parents=True, exist_ok=True)
        self.draft.write_text(DRAFT, encoding="utf-8")
        self.out = self.state / mig.STAGE

    def snapshot(self) -> dict:
        return {p.relative_to(self.vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.vault.rglob("*") if p.is_file() and ".git" not in p.parts}

    def plan(self, **kw):
        plan, _contents, _draft = mig.build_plan(self.vault, self.root, RULES, index_draft=kw.get("draft", self.draft),
                                                 also_delete=kw.get("also_delete", ()))
        plan["run_id"] = "run-1"
        return plan

    def apply(self, plan, count=None):
        return mig.apply(self.vault, self.root, plan, plan["counts"]["files"] if count is None else count,
                         self.out, RULES, log_root=self.logs, lock_root=self.locks)

    def changes(self, plan, rel):
        return [(c["from"], c["to"]) for link in plan["links"] if link["rel"] == rel for c in link["changes"]]

    def regenerate(self, also_fix: bool):
        """What the dreaming pass and needs-review write after the apply."""
        maps = self.root / "memory" / "mocs"
        (maps / "workflow.md").write_text("---\nkind: moc\n---\n\n# workflow\n\n[[moc-root]] · [[moc-memory]]\n", encoding="utf-8")
        (maps / "moc-memory.md").write_text("---\nkind: moc\n---\n\n# memory\n\n[[moc-root]]\n", encoding="utf-8")
        (maps / "needs-review.md").write_text("---\nkind: moc\n---\n\n# Needs review\n\n[[moc-root]]\n", encoding="utf-8")
        (maps / "moc-root.md").write_text("---\nkind: moc\n---\n\n# root\n\n## Memory\n\n- [[moc-memory]]\n- [[needs-review]]\n\n"
                                          "## Calendar\n\n- [[moc-calendar-2026]]\n", encoding="utf-8")
        (self.vault / "Calendar" / "moc-calendar-2026.md").write_text(
            "---\nkind: moc\n---\n\n# 2026\n\n[[moc-root]]\n\n## 2026-08\n\n- [[2026-08-10-diary]] — diary\n", encoding="utf-8")
        if also_fix and (maps / "fix.md").exists():
            (maps / "fix.md").unlink()


class DryRun(Fixture):
    def test_it_plans_what_goes_and_where_every_link_points_and_writes_nothing(self):
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(self.snapshot(), before, "the dry run wrote to the vault")
        self.assertEqual({d["rel"] for d in plan["deletions"]},
                         {"Agent/Home.md", "Filing.md", "Agent/memory/mocs/workflow-2.md"})
        for d in plan["deletions"]:
            self.assertRegex(d["restore"], r"^[0-9a-f]{40}$")
        self.assertEqual([h["rel"] for h in plan["held"]], ["Agent/memory/mocs/fix.md"])
        self.assertIn("--also-delete fix", plan["held"][0]["why"])
        self.assertEqual([(m["from"], m["to"]) for m in plan["moves"]],
                         [("Calendar/2026-08-10.md", "Calendar/2026/2026-08-10-diary.md")])
        self.assertEqual(self.changes(plan, "Projects/agentm/decisions/memory-os-architecture-scan.md"),
                         [("[[Home]]", "[[moc-root\\|Home]]"), ("[[Home]]", "[[moc-root|Home]]")],
                         "the table row needs the escaped pipe and the list item the plain one")
        self.assertEqual(self.changes(plan, "Projects/index.md"), [("[[Filing]]", "[[../index|Filing]]")])
        self.assertEqual(self.changes(plan, "Agent/memory/semantic/blog-author.md"), [("[[Home]]", "[[moc-root|Home]]")])
        self.assertEqual(self.changes(plan, "Agent/memory/episodic/trace.md"), [("[[workflow-2]]", "[[workflow]]")],
                         "a code span was repointed, or a held page's link was")
        touched = {link["rel"] for link in plan["links"]}
        self.assertFalse(any("_harness" in rel or "/mocs/" in rel for rel in touched), touched)
        self.assertEqual(plan["not_written"], [])
        self.assertEqual(plan["counts"], {"deletions": 3, "moves": 1, "links": 8, "index": 1, "files": 13})
        lines = mig.manifest(plan, self.vault)
        self.assertEqual(len(lines), 5)
        self.assertTrue(all("Restore: `git -C" in line for line in lines), lines)

    def test_a_page_under_the_threshold_goes_only_on_the_operators_word(self):
        plan = self.plan(also_delete=["fix"])
        self.assertIn("Agent/memory/mocs/fix.md", {d["rel"] for d in plan["deletions"]})
        self.assertEqual(plan["held"], [])
        self.assertIn(("[[fix]]", "[[moc-memory]]"), self.changes(plan, "Agent/memory/episodic/trace.md"))

    def test_an_uncommitted_change_to_what_goes_refuses(self):
        (self.vault / "Filing.md").write_text("# Filing, edited and not committed\n", encoding="utf-8")
        with self.assertRaises(mig.Refused):
            self.plan()

    def test_a_ruling_note_that_moved_refuses(self):
        p = self.vault / "Projects/agentm/decisions/liteparse-doc-ingestion-candidate.md"
        p.write_text(p.read_text(encoding="utf-8") + "- and [[Home]] again\n", encoding="utf-8")
        _git(self.vault, "commit", "-q", "-am", "an edit")
        with self.assertRaises(mig.Refused):
            self.plan()

    def test_a_draft_that_still_names_a_retired_note_refuses(self):
        self.draft.write_text(DRAFT + "\nSee [[Filing]].\n", encoding="utf-8")
        with self.assertRaises(mig.Refused):
            self.plan()

    def test_a_link_outside_the_scope_is_listed_not_written(self):
        (self.vault / "Projects" / "notes.md").write_text("- [[workflow-2]] and [[Home]]\n", encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "a note")
        plan = self.plan()
        self.assertEqual([(n["rel"], n["link"]) for n in plan["not_written"]],
                         [("Projects/notes.md", "[[workflow-2]]"), ("Projects/notes.md", "[[Home]]")])
        self.assertNotIn("Projects/notes.md", {link["rel"] for link in plan["links"]})


class ApplyAndRevert(Fixture):
    def test_apply_refuses_a_wrong_count_and_a_vault_that_moved(self):
        plan = self.plan()
        before = self.snapshot()
        with self.assertRaises(mig.Refused):
            self.apply(plan, count=plan["counts"]["files"] + 1)
        (self.vault / "Projects/agentm/pattern/corpus-screening-funnel.md").write_text("- [[moc-root|Home]]\n", encoding="utf-8")
        _git(self.vault, "commit", "-q", "-am", "moved under the plan")
        moved = self.snapshot()
        with self.assertRaises(mig.Refused):
            self.apply(plan)
        self.assertEqual(self.snapshot(), moved, "a refused apply wrote")
        self.assertNotEqual(before, moved)

    def test_apply_lands_the_plan_and_revert_brings_every_byte_back(self):
        before = self.snapshot()
        diary = (self.vault / "Calendar/2026-08-10.md").read_bytes()
        plan = self.plan()
        journal = self.apply(plan)
        self.assertTrue(journal["matches_dry_run"], journal)
        for gone in ("Agent/Home.md", "Filing.md", "Agent/memory/mocs/workflow-2.md", "Calendar/2026-08-10.md"):
            self.assertFalse((self.vault / gone).exists(), gone)
        self.assertEqual((self.vault / "Calendar/2026/2026-08-10-diary.md").read_bytes(), diary, "the move changed the body")
        self.assertEqual((self.vault / "index.md").read_text(encoding="utf-8"), DRAFT)
        self.assertIn("[[moc-root\\|Home]] MOC",
                      (self.vault / "Projects/agentm/decisions/memory-os-architecture-scan.md").read_text(encoding="utf-8"))
        self.assertIn("A record keeps [[Home]] and [[Filing]].",
                      (self.vault / "Projects/agentm/_harness/progress-x.md").read_text(encoding="utf-8"))
        mig.revert(self.vault, self.root, "run-1", log_root=self.logs, lock_root=self.locks)
        self.assertEqual(self.snapshot(), before, "the revert did not restore every file")


class Finish(Fixture):
    def test_the_marker_waits_for_every_post_condition(self):
        plan = self.plan(also_delete=["fix"])
        self.assertTrue(self.apply(plan)["matches_dry_run"])
        problems = mig.finish(self.vault, self.root, plan, self.out, RULES)
        self.assertTrue(any("moc-root.md: missing" in p for p in problems), problems)
        self.assertFalse(ms.data_run_done(self.root), "the marker was written before the maps were regenerated")
        self.regenerate(also_fix=True)
        problems = mig.finish(self.vault, self.root, plan, self.out, RULES)
        self.assertEqual(problems, [])
        self.assertTrue(ms.data_run_done(self.root))

    def test_a_held_page_keeps_the_marker_off(self):
        plan = self.plan()
        self.apply(plan)
        self.regenerate(also_fix=False)
        problems = mig.finish(self.vault, self.root, plan, self.out, RULES)
        self.assertTrue(any("fix.md" in p and "under the page threshold" in p for p in problems), problems)
        self.assertFalse(ms.data_run_done(self.root))

    def test_a_root_map_missing_an_area_map_keeps_the_marker_off(self):
        plan = self.plan(also_delete=["fix"])
        self.apply(plan)
        self.regenerate(also_fix=True)
        (self.root / "diagnostics").mkdir()
        (self.root / "diagnostics" / "moc-diagnostics.md").write_text("---\nkind: moc\n---\n", encoding="utf-8")
        problems = mig.finish(self.vault, self.root, plan, self.out, RULES)
        self.assertIn("memory/mocs/moc-root.md: does not list moc-diagnostics", problems)
        self.assertFalse(ms.data_run_done(self.root))

    def test_finish_refuses_before_an_apply(self):
        plan = self.plan()
        with self.assertRaises(mig.Refused):
            mig.finish(self.vault, self.root, plan, self.out, RULES)


if __name__ == "__main__":
    unittest.main()
