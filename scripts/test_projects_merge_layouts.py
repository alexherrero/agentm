#!/usr/bin/env python3
"""Filing-v2 part 2b, task 2: readers are tolerant of the vault-root
`Projects/` generation — a SIBLING of the memory root — while `desk/projects`
still holds the tree.

Pins, across the merge window:
  - the seam chokepoint (`harness_memory.resolve_project`) resolves a project
    on the root generation, flat or nested, and the root wins a slug held by
    both; the near-empty root shell does not hide a project still on desk;
    `harness_state_dir` composes the root path through the sibling backend;
  - walkers take the union of both spaces and key root-space entries relative
    to the vault root;
  - a project's own tree resolves on whichever generation holds it.

Run: python3 scripts/test_projects_merge_layouts.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (str(_HERE), str(_SKILL)):
    if p not in sys.path:
        sys.path.insert(0, p)

import harness_memory as hm  # noqa: E402
import arc_registry  # noqa: E402
import frontmatter_validator  # noqa: E402
import graph_snapshot  # noqa: E402
import migrate_arcs  # noqa: E402
import vault_lint  # noqa: E402
from vault_backend_stub import VaultBackend  # noqa: E402


def _project_root(tmp: Path, slug: str = "fixture") -> Path:
    root = tmp / "repo"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "project.json").write_text(
        json.dumps({"vault_project": slug}), encoding="utf-8")
    return root


class ResolveProjectAcrossGenerations(unittest.TestCase):
    def _resolve(self, backend, project_root):
        with mock.patch("backend_selection.select_backend", return_value=backend):
            return hm.resolve_project({"cwd": project_root})

    def test_flat_layout_root_space_resolves_on_the_primary_backend(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vault = tmp / "vault"
            (vault / "Projects" / "fixture" / "_harness").mkdir(parents=True)
            res = self._resolve(VaultBackend(root=vault), _project_root(tmp))
            self.assertEqual(res["layout"], "root")
            self.assertEqual(res["project_locator"].key, "Projects/fixture")
            self.assertEqual(Path(res["backend"].root), vault)

    def test_nested_layout_root_space_resolves_on_a_sibling_backend(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            memory_root.mkdir(parents=True)
            (tmp / "Vault" / "Projects" / "fixture" / "_harness").mkdir(parents=True)
            res = self._resolve(VaultBackend(root=memory_root), _project_root(tmp))
            self.assertEqual(res["layout"], "root")
            self.assertEqual(Path(res["backend"].root), tmp / "Vault")
            self.assertEqual(hm.harness_state_dir(res),
                             tmp / "Vault" / "Projects" / "fixture" / "_harness")

    def test_window_root_shell_does_not_hide_a_project_still_on_desk(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            (memory_root / "desk" / "projects" / "fixture" / "_harness").mkdir(parents=True)
            (tmp / "Vault" / "Projects").mkdir(parents=True)
            (tmp / "Vault" / "Projects" / "index.md").write_text("# Projects\n", encoding="utf-8")
            res = self._resolve(VaultBackend(root=memory_root), _project_root(tmp))
            self.assertEqual(res["layout"], "new")
            self.assertEqual(hm.harness_state_dir(res),
                             memory_root / "desk" / "projects" / "fixture" / "_harness")

    def test_root_generation_wins_a_slug_held_by_both(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            (memory_root / "desk" / "projects" / "fixture").mkdir(parents=True)
            (tmp / "Vault" / "Projects" / "fixture").mkdir(parents=True)
            res = self._resolve(VaultBackend(root=memory_root), _project_root(tmp))
            self.assertEqual(res["layout"], "root")


class WalkersUnionBothSpaces(unittest.TestCase):
    def test_arc_registry_counts_root_and_desk_notes_and_keys_root_by_vault_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            desk_note = memory_root / "desk" / "projects" / "p" / "decisions" / "a.md"
            root_note = tmp / "Vault" / "Projects" / "q" / "decisions" / "b.md"
            for note in (desk_note, root_note):
                note.parent.mkdir(parents=True)
                note.write_text("---\narc: some-arc\n---\n\nbody\n", encoding="utf-8")
            with mock.patch.object(arc_registry, "known_arcs", return_value=frozenset({"some-arc"})):
                result = arc_registry.audit(memory_root)
            self.assertEqual(result["by_arc"], {"some-arc": 2})
            self.assertEqual(result["total_stamped"], 2)
            self.assertEqual(arc_registry._vault_rel(root_note, memory_root), "Projects/q/decisions/b.md")
            self.assertEqual(arc_registry._vault_rel(desk_note, memory_root), "desk/projects/p/decisions/a.md")

    def test_scope_roots_gain_the_root_sibling_when_the_scope_names_projects(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            (memory_root / "desk" / "projects").mkdir(parents=True)
            (memory_root / "memory").mkdir(parents=True)
            (tmp / "Vault" / "Projects").mkdir(parents=True)
            fm = frontmatter_validator._scope_roots(memory_root, ("memory", "desk/projects"))
            vl = vault_lint._scope_roots(memory_root, ["memory", "desk/projects"])
            self.assertIn(tmp / "Vault" / "Projects", fm)
            self.assertIn(tmp / "Vault" / "Projects", vl)
            # A scope that does not name the project space stays inside the memory root.
            self.assertNotIn(tmp / "Vault" / "Projects",
                             frontmatter_validator._scope_roots(memory_root, ("memory",)))

    def test_project_tree_resolves_on_whichever_generation_holds_it(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            (tmp / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
            memory_root.mkdir(parents=True)
            self.assertEqual(migrate_arcs._project_root(memory_root, "p"),
                             memory_root / "desk/projects" / "p")
            (tmp / "Vault" / "Projects" / "p").mkdir(parents=True)
            self.assertEqual(migrate_arcs._project_root(memory_root, "p"),
                             tmp / "Vault" / "Projects" / "p")


class GroupValuesDeriveTheSlugOnEitherGeneration(unittest.TestCase):
    def test_graph_snapshot_project_from_group(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            for group, want in (("desk/projects/agentm/decisions", "agentm"),
                                ("Projects/agentm/decisions", "agentm"),
                                ("memory/semantic", None)):
                note.write_text(f"---\nkind: decision\ngroup: {group}\n---\n\nbody\n", encoding="utf-8")
                meta = graph_snapshot._extract_meta_from_file(note)
                self.assertEqual(meta.get("project"), want, group)


class WritersFollowTheTree(unittest.TestCase):
    """Task 3: after the move, writers stamp the group from where the project
    lives and land in the vault-root space; a flat fixture on the old layout
    keeps its desk behaviour (discovered, never conjured)."""

    def _nested(self, td: Path, *, project: str | None = None) -> Path:
        memory_root = td / "Vault" / "Agent"
        (td / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
        (memory_root / "memory").mkdir(parents=True)
        (td / "Vault" / "Projects").mkdir(parents=True)
        if project:
            (td / "Vault" / "Projects" / project).mkdir()
        return memory_root

    def test_save_lands_a_projects_group_in_the_vault_root_space(self):
        import save
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._nested(Path(td), project="agentm")
            target = save.group_target_dir(memory_root, "projects/agentm/decisions")
            self.assertEqual(target, Path(td) / "Vault" / "Projects" / "agentm" / "decisions")
            # A desk group is untouched; a flat vault keeps the space inside itself.
            self.assertEqual(save.group_target_dir(memory_root, "desk/projects/x"),
                             memory_root / "desk/projects/x")
            flat = Path(td) / "flat"
            flat.mkdir()
            self.assertEqual(save.group_target_dir(flat, "projects/p"), flat / "Projects" / "p")

    def test_group_segment_follows_the_project_home(self):
        import memory_mcp_tools as mcp
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._nested(Path(td), project="moved")
            (memory_root / "desk" / "projects" / "stayed").mkdir(parents=True)
            self.assertEqual(mcp._project_group_segment(memory_root, "moved"), "projects")
            self.assertEqual(mcp._project_group_segment(memory_root, "stayed"), "desk/projects")
            self.assertEqual(mcp._project_group_segment(memory_root, "brand-new"), "projects")
            self.assertEqual(hm._project_group_segment(memory_root, "stayed"), "desk/projects")
            self.assertEqual(hm._project_group_segment(memory_root, "brand-new"), "projects")

    def test_promotion_creates_the_project_in_the_root_space(self):
        import promote
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._nested(Path(td))
            (memory_root / "desk" / "tasks" / "widget").mkdir(parents=True)
            res = promote.promote(memory_root, promote.Promotion(
                task="widget", project="widget", documents={"README.md": "hello\n"}))
            self.assertTrue((Path(td) / "Vault" / "Projects" / "widget" / "README.md").is_file())
            self.assertIn("../Projects/widget/README.md", res.written)
            marker = (memory_root / "desk" / "tasks" / "widget" / promote.PROMOTED_MARKER).read_text()
            self.assertIn("[[Projects/widget]]", marker)

    def test_moc_arcs_index_lands_in_the_moved_project(self):
        import moc_generator
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._nested(Path(td), project="agentm")
            self.assertEqual(moc_generator._project_home(memory_root, "agentm"),
                             Path(td) / "Vault" / "Projects" / "agentm")
            self.assertEqual(moc_generator._project_group(memory_root, "agentm"), "projects")
            self.assertEqual(moc_generator._project_home(memory_root, "elsewhere"),
                             memory_root / "desk/projects" / "elsewhere")

    def test_new_project_resolves_into_the_root_space_when_it_exists(self):
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._nested(Path(td))
            backend = VaultBackend(root=memory_root)
            with mock.patch("backend_selection.select_backend", return_value=backend):
                res = hm.resolve_project({"cwd": _project_root(Path(td), "fresh")})
            self.assertEqual(res["layout"], "root")
            self.assertEqual(hm.harness_state_dir(res),
                             Path(td) / "Vault" / "Projects" / "fresh" / "_harness")

    def test_lowercase_root_group_derives_the_slug(self):
        import recall
        self.assertEqual(recall._derive_project("projects/agentm/decisions"), "agentm")


class ConsistencyGateKnowsTheSibling(unittest.TestCase):
    """check-memory-root-consistency: the projects space may sit BESIDE
    memory_root by design, but only when both halves name the same sibling."""

    def _run(self, config: dict) -> int:
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".agentm-config.json").write_text(json.dumps(config), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(_HERE / "check-memory-root-consistency.py")],
                env={**__import__("os").environ, "AGENTM_INSTALL_PREFIX": td},
                capture_output=True, text=True)
            return proc.returncode

    def test_sibling_projects_space_passes_when_the_python_side_agrees(self):
        base = {"plugins.obsidian-vault.memory_root": "Agent",
                "daemon.spaces": {"memory": "Agent/memory", "projects": "Projects"}}
        self.assertEqual(self._run({**base, "plugins.obsidian-vault.spaces": {"memory": "memory"}}), 0)
        self.assertEqual(self._run({**base, "plugins.obsidian-vault.spaces":
                                    {"memory": "memory", "projects": "../Projects"}}), 0)

    def test_sibling_projects_space_fails_when_the_halves_disagree(self):
        self.assertEqual(self._run({
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Agent/memory", "projects": "Projects"},
            "plugins.obsidian-vault.spaces": {"memory": "memory", "projects": "desk/projects"},
        }), 1)

    def test_any_other_space_outside_the_root_still_fails(self):
        self.assertEqual(self._run({
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Elsewhere/memory"},
        }), 1)


class RecallCorpusReachesTheRootSpace(unittest.TestCase):
    """The Python recall (fallback, CLI, eval) walks the vault-root Projects/
    sibling too, keys its notes vault-root-relative, and reads them through
    the backend that owns them."""

    def _vault(self, td: Path) -> Path:
        memory_root = td / "Vault" / "Agent"
        (td / "Vault" / ".obsidian").mkdir(parents=True, exist_ok=True)
        (memory_root / "memory" / "semantic").mkdir(parents=True)
        (memory_root / "memory" / "semantic" / "inside.md").write_text(
            "---\nkind: reference\nstatus: active\ncreated: 2026-09-01\n"
            "tags: [zebra]\ngroup: memory\nslug: inside\n---\n\nzebra facts inside\n",
            encoding="utf-8")
        root_note = td / "Vault" / "Projects" / "agentm" / "decisions" / "moved.md"
        root_note.parent.mkdir(parents=True)
        root_note.write_text(
            "---\nkind: decision\nstatus: active\ncreated: 2026-09-01\n"
            "tags: [zebra]\ngroup: projects/agentm/decisions\nslug: moved\n---\n\nzebra decision moved\n",
            encoding="utf-8")
        return memory_root

    def test_walk_includes_root_space_notes_with_memory_root_relative_keys(self):
        import recall
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._vault(Path(td))
            keys = sorted(recall._vault_rel(p, memory_root) for p in recall._iter_entry_paths(memory_root))
            self.assertEqual(keys, ["../Projects/agentm/decisions/moved.md", "memory/semantic/inside.md"])

    def test_grep_search_finds_a_root_space_note(self):
        import recall
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._vault(Path(td))
            hits = recall._grep_search(memory_root, ["zebra"])
            self.assertIn("../Projects/agentm/decisions/moved.md", hits)
            self.assertIn("memory/semantic/inside.md", hits)

    def test_eval_expected_paths_follow_the_merge(self):
        sys.path.insert(0, str(_HERE / "health"))
        import eval_v6_retrieval as ev
        self.assertEqual(ev._resolve_expected_path("desk/projects/agentm/decisions/x.md"),
                         "../Projects/agentm/decisions/x.md")
        self.assertEqual(ev._resolve_expected_path("<vault>/decisions/x.md"),
                         "../Projects/agentm/decisions/x.md")
        self.assertEqual(ev._resolve_expected_path("memory/semantic/y.md"), "memory/semantic/y.md")
        with tempfile.TemporaryDirectory() as td:
            memory_root = self._vault(Path(td))
            self.assertTrue(ev._expected_exists(memory_root, "../Projects/agentm/decisions/moved.md"))
            self.assertTrue(ev._expected_exists(memory_root, "memory/semantic/inside.md"))
            self.assertFalse(ev._expected_exists(memory_root, "../Projects/agentm/decisions/gone.md"))



class RootSpaceNeedsTheVaultWitness(unittest.TestCase):
    """A directory named `Projects` beside the memory root is the vault's only
    when the memory root is nested inside an Obsidian vault — `.obsidian/` at
    the parent, none at the memory root. A flat vault's parent is the
    operator's home, where `Projects/` is common; it is never probed (2b
    review, defect 1)."""

    def _flat_home(self, tmp: Path) -> tuple:
        home = tmp / "home"
        vault = home / "Vault"
        (vault / ".obsidian").mkdir(parents=True)  # the vault root IS the memory root
        (vault / "memory").mkdir()
        (home / "Projects" / "agentm" / "_harness").mkdir(parents=True)  # the operator's own repos
        return home, vault

    def test_resolve_project_never_probes_above_a_flat_vault(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home, vault = self._flat_home(tmp)
            with mock.patch("backend_selection.select_backend", return_value=VaultBackend(root=vault)):
                res = hm.resolve_project({"cwd": _project_root(tmp, "agentm")})
            self.assertNotEqual(res["layout"], "root")
            self.assertEqual(Path(res["backend"].root), vault)
            self.assertNotEqual(hm._project_group_segment(vault, "agentm"), "projects")

    def test_writers_and_walkers_ignore_the_operators_projects_dir(self):
        import kind_registry
        import moc_generator
        import promote
        import recall
        import save
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home, vault = self._flat_home(tmp)
            self.assertIsNone(hm._root_projects_dir(vault))
            self.assertEqual(save.root_space_dir(vault), vault / "Projects")
            self.assertEqual(promote.project_dir_for(vault, "w"), ("desk/projects/w", "desk/projects/w"))
            self.assertEqual(migrate_arcs._project_root(vault, "agentm"), vault / "desk/projects" / "agentm")
            self.assertEqual(moc_generator._project_home(vault, "agentm"), vault / "desk/projects" / "agentm")
            for roots in (arc_registry._walk_roots(vault), kind_registry._walk_roots(vault),
                          moc_generator._walk_roots(vault),
                          frontmatter_validator._scope_roots(vault, ("memory", "desk/projects")),
                          vault_lint._scope_roots(vault, ["memory", "desk/projects"])):
                self.assertNotIn(home / "Projects", roots)
            self.assertFalse(recall._under_root_projects(home / "Projects" / "agentm" / "x.md", vault))

    def test_a_nested_root_without_the_witness_is_not_a_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
            memory_root.mkdir(parents=True)
            (tmp / "Vault" / "Projects" / "p").mkdir(parents=True)  # no .obsidian anywhere
            self.assertIsNone(hm._root_projects_dir(memory_root))
            self.assertNotIn(tmp / "Vault" / "Projects", arc_registry._walk_roots(memory_root))


class FlatRootSpaceIsDiscoveredEverywhere(unittest.TestCase):
    """`<memory-root>/Projects` — a flat vault's root space — is a generation
    every reader and writer sees, not only the nested sibling (2b review,
    defects 3 and 5)."""

    def _flat(self, tmp: Path) -> Path:
        vault = tmp / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / "memory").mkdir()
        note = vault / "Projects" / "agentm" / "decisions" / "x.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\narc: some-arc\nkind: decision\n---\n\nbody\n", encoding="utf-8")
        return vault

    def test_walkers_and_writers_see_the_flat_root_space(self):
        import kind_registry
        import moc_generator
        import promote
        import save
        with tempfile.TemporaryDirectory() as td:
            vault = self._flat(Path(td))
            self.assertEqual(hm._root_projects_dir(vault), vault / "Projects")
            self.assertEqual(hm._project_group_segment(vault, "agentm"), "projects")
            self.assertEqual(save.root_space_dir(vault), vault / "Projects")
            self.assertEqual(promote.project_dir_for(vault, "w"), ("Projects/w", "Projects/w"))
            self.assertEqual(migrate_arcs._project_root(vault, "agentm"), vault / "Projects" / "agentm")
            self.assertEqual(moc_generator._project_group(vault, "agentm"), "projects")
            for roots in (arc_registry._walk_roots(vault), kind_registry._walk_roots(vault),
                          moc_generator._walk_roots(vault),
                          frontmatter_validator._scope_roots(vault, ("memory", "desk/projects")),
                          vault_lint._scope_roots(vault, ["memory", "desk/projects"])):
                self.assertIn(vault / "Projects", roots)
            with mock.patch.object(arc_registry, "known_arcs", return_value=frozenset({"some-arc"})):
                self.assertEqual(arc_registry.audit(vault)["total_stamped"], 1)


class MigrateArcsOnARootSpaceProject(unittest.TestCase):
    """migrate_arcs keys root-space paths relative to the vault root and
    re-roots them through the sibling on apply (2b review, defect 2)."""

    def _nested(self, tmp: Path) -> tuple:
        memory_root = tmp / "Vault" / "Agent"
        (memory_root / "memory").mkdir(parents=True)
        (tmp / "Vault" / ".obsidian").mkdir()
        proj = tmp / "Vault" / "Projects" / "agentm"
        (proj / "decisions").mkdir(parents=True)
        (proj / "decisions" / "d1.md").write_text("---\narc: some-arc\n---\n\nbody\n", encoding="utf-8")
        (proj / "_harness" / "archive").mkdir(parents=True)
        (proj / "_harness" / "archive" / "PLAN.archive.20260101-widget.md").write_text("# plan\n", encoding="utf-8")
        (proj / "_harness" / "designs" / "some-arc").mkdir(parents=True)
        (proj / "_harness" / "designs" / "some-arc" / "d.md").write_text(
            "see _harness/designs/some-arc/d.md\n", encoding="utf-8")
        (memory_root / "memory" / "ref.md").write_text("link: _harness/designs/some-arc/d.md\n", encoding="utf-8")
        return memory_root, proj

    def test_plan_stamp_keys_root_space_rows_relative_to_the_vault_root(self):
        with tempfile.TemporaryDirectory() as td:
            memory_root, proj = self._nested(Path(td))
            plan = migrate_arcs.plan_stamp(memory_root, "agentm")
            self.assertEqual(plan.errors, [])
            self.assertEqual([r.path for r in plan.rows], ["Projects/agentm/decisions/d1.md"])
            self.assertEqual(migrate_arcs._vault_base(memory_root, plan.rows[0].path), memory_root.parent)

    def test_apply_stamp_re_roots_a_root_space_row(self):
        with tempfile.TemporaryDirectory() as td:
            memory_root, proj = self._nested(Path(td))
            note = proj / "decisions" / "d2.md"
            note.write_text("---\ntags: [x]\n---\n\nbody\n", encoding="utf-8")
            plan = migrate_arcs.Plan()
            plan.rows.append(migrate_arcs.MappingRow("Projects/agentm/decisions/d2.md", "", "some-arc", "HIGH"))
            migrate_arcs.apply_stamp(memory_root, plan)
            self.assertIn("arc: some-arc", note.read_text(encoding="utf-8"))

    def test_archive_group_and_designs_move_work_on_a_root_space_project(self):
        with tempfile.TemporaryDirectory() as td:
            memory_root, proj = self._nested(Path(td))
            with mock.patch.object(migrate_arcs.arc_registry, "known_arcs", return_value=frozenset({"widget"})):
                plan = migrate_arcs.plan_archive_group(memory_root, "agentm")
            self.assertEqual(plan.errors, [])
            self.assertEqual(plan.rows[0].path, "Projects/agentm/_harness/archive/PLAN.archive.20260101-widget.md")
            migrate_arcs.apply_archive_group(memory_root, plan)
            self.assertTrue((proj / "_harness" / "archive" / "widget" / "PLAN.archive.20260101-widget.md").is_file())

            plan = migrate_arcs.plan_designs_move(memory_root, "agentm", "some-arc")
            self.assertEqual(plan.errors, [])
            paths = [r.path for r in plan.rows]
            self.assertEqual(paths[0], "Projects/agentm/_harness/designs/some-arc")
            self.assertIn("memory/ref.md", paths)
            self.assertIn("Projects/agentm/_harness/designs/some-arc/d.md", paths)
            migrate_arcs.apply_designs_move(memory_root, "agentm", "some-arc", plan)
            moved = proj / "_harness" / "archive" / "designs" / "some-arc" / "d.md"
            self.assertTrue(moved.is_file())
            self.assertIn("_harness/archive/designs/some-arc/", moved.read_text(encoding="utf-8"))
            self.assertIn("_harness/archive/designs/some-arc/",
                          (memory_root / "memory" / "ref.md").read_text(encoding="utf-8"))


class RootSpaceHelperCopiesAgree(unittest.TestCase):
    """The one predicate is vendored per file (the skill scripts stay
    self-contained); the copies must not drift."""

    FILES = ("scripts/harness_memory.py",) + tuple(
        f"harness/skills/memory/scripts/{n}" for n in (
            "save.py", "promote.py", "ideas_promote.py", "moc_generator.py", "arc_registry.py",
            "kind_registry.py", "frontmatter_validator.py", "vault_lint.py", "graph_snapshot.py",
            "recall.py", "migrate_arcs.py"))

    def test_every_copy_is_byte_identical(self):
        import re
        bodies = {}
        for rel in self.FILES:
            text = (_HERE.parent / rel).read_text(encoding="utf-8")
            m = re.search(r"^def _root_projects_dir\(vault\):\n(?:    .*\n|\n)*?    return None\n", text, re.M)
            m2 = re.search(r"^def _is_dir_exact\(path\):\n(?:    .*\n|\n)*?        return False\n", text, re.M)
            self.assertIsNotNone(m, rel)
            self.assertIsNotNone(m2, rel)
            bodies[rel] = m.group(0) + m2.group(0)
        self.assertEqual(len(set(bodies.values())), 1, sorted(bodies))

    def test_the_flat_rung_matches_the_directorys_exact_case(self):
        """A V4-era `projects/` rung is not the flat root space, whatever the
        filesystem's case rules say."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            (vault / "projects" / "legacy" / "_harness").mkdir(parents=True)
            self.assertIsNone(hm._root_projects_dir(vault))
            with mock.patch("backend_selection.select_backend", return_value=VaultBackend(root=vault)):
                res = hm.resolve_project({"cwd": _project_root(Path(td), "legacy")})
            self.assertNotEqual(res["layout"], "root")
            self.assertNotEqual(res["project_locator"].key.split("/")[0], "Projects")
            self.assertNotEqual(hm._project_group_segment(vault, "legacy"), "projects")
            for roots in (arc_registry._walk_roots(vault), frontmatter_validator._scope_roots(vault, ("desk/projects",)),
                          vault_lint._scope_roots(vault, ["desk/projects"])):
                self.assertNotIn(vault / "Projects", roots)

if __name__ == "__main__":
    unittest.main()
