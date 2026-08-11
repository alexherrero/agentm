#!/usr/bin/env python3
"""V5-3 A2 index-invariant gate.

Asserts that a derived SQLite store lives at a device-local path, never
inside the vault — the Tier.LOCAL_INDEX contract (storage_seam.py). SQLite
on cloud-sync is a known corruption pattern, so the rule is about the file
type, not about any one feature.

This gate was written against `vec_index.py`, which owned the only such store
and the helper that placed it. That module was removed with the vector stack
(see wiki/designs/agentm-rescope-week1-experiment.md), and `graph_snapshot.py`
inherited both the helper and the contract — its snapshot DB sits in the same
device-local root under the same rule. The invariant outlived the subsystem
that motivated it, so it is pinned against its current owner rather than
retired alongside the old one.

Runs entirely from path arithmetic — no backend required.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# graph_snapshot lives in harness/skills/memory/scripts/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_SCRIPTS = _REPO_ROOT / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import graph_snapshot  # noqa: E402


class TestA2IndexInvariant(unittest.TestCase):
    """The A2 index invariant: derived stores are device-local, never in the vault."""

    def test_snapshot_path_not_inside_vault(self) -> None:
        """The snapshot path must not be a descendant of the vault dir."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            snap = graph_snapshot._snapshot_path(vault)
            try:
                snap.relative_to(vault)
                self.fail(
                    f"_snapshot_path({vault}) = {snap} is inside the vault — "
                    "SQLite on cloud-sync is a corruption pattern. "
                    "Derived stores must live under ~/.agentm/memory/_meta/."
                )
            except ValueError:
                pass  # relative_to raises ValueError when not a descendant

    def test_snapshot_path_is_device_local(self) -> None:
        """The snapshot path must be under ~/.agentm/memory/_meta/."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            snap = graph_snapshot._snapshot_path(vault)
            expected_root = Path.home() / ".agentm" / "memory" / "_meta"
            try:
                snap.relative_to(expected_root)
            except ValueError:
                self.fail(
                    f"_snapshot_path({vault}) = {snap} is not under "
                    f"{expected_root}. Derived stores must be device-local."
                )

    def test_two_vaults_have_distinct_store_paths(self) -> None:
        """Different vault paths must produce different local store dirs."""
        with tempfile.TemporaryDirectory() as tmp:
            v1 = Path(tmp) / "VaultA"
            v2 = Path(tmp) / "VaultB"
            v1.mkdir()
            v2.mkdir()
            self.assertNotEqual(
                graph_snapshot._snapshot_path(v1),
                graph_snapshot._snapshot_path(v2),
                "Two different vaults must not share a store path.",
            )

    def test_rebuild_leaves_no_sqlite_file_in_the_vault(self) -> None:
        """A real rebuild must not create any .db inside the vault.

        The strongest form of the invariant: not "the path arithmetic is
        right" but "running the thing did not write a database into a synced
        directory." Path arithmetic can be correct while some other write in
        the same function lands in the wrong place.
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            (vault / "memory" / "reference").mkdir(parents=True)
            (vault / "memory" / "reference" / "note.md").write_text(
                "---\nkind: reference\nslug: note\n---\n\nbody\n", encoding="utf-8"
            )
            graph_snapshot.rebuild(vault)
            strays = sorted(p for p in vault.rglob("*.db"))
            self.assertEqual(
                strays, [],
                f"rebuild() wrote SQLite file(s) inside the vault: {strays}. "
                "Derived stores must be device-local only.",
            )


if __name__ == "__main__":
    unittest.main()
