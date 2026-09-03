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
            (memory_root / "desk" / "projects" / "fixture").mkdir(parents=True)
            (tmp / "Vault" / "Projects" / "fixture").mkdir(parents=True)
            res = self._resolve(VaultBackend(root=memory_root), _project_root(tmp))
            self.assertEqual(res["layout"], "root")


class WalkersUnionBothSpaces(unittest.TestCase):
    def test_arc_registry_counts_root_and_desk_notes_and_keys_root_by_vault_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            memory_root = tmp / "Vault" / "Agent"
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


if __name__ == "__main__":
    unittest.main()
