#!/usr/bin/env python3
"""V5-3 A2 index-invariant gate.

Asserts that the vector index lives at a device-local path (never inside
the vault) — Tier.LOCAL_INDEX contract (storage_seam.py).  The embedding
queue stays vault-local; only the SQLite DB must not sync.

Runs entirely from path arithmetic — no sqlite-vec required.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# The vec_index module lives in harness/skills/memory/scripts/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VEC_SCRIPTS = _REPO_ROOT / "harness" / "skills" / "memory" / "scripts"
if str(_VEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_VEC_SCRIPTS))

import vec_index  # noqa: E402


class TestA2IndexInvariant(unittest.TestCase):
    """The A2 index invariant: index is device-local, never in the vault."""

    def test_index_path_not_inside_vault(self) -> None:
        """_index_path(vault) must not be a descendant of the vault dir."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            idx = vec_index._index_path(vault)
            # The index must NOT be inside the vault directory.
            try:
                idx.relative_to(vault)
                self.fail(
                    f"_index_path({vault}) = {idx} is inside the vault — "
                    "SQLite on cloud-sync is a corruption pattern. "
                    "The index must live under ~/.agentm/memory/_meta/."
                )
            except ValueError:
                pass  # relative_to raises ValueError when not a descendant

    def test_index_path_is_device_local(self) -> None:
        """_index_path(vault) must be under ~/.agentm/memory/_meta/."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            idx = vec_index._index_path(vault)
            expected_root = Path.home() / ".agentm" / "memory" / "_meta"
            try:
                idx.relative_to(expected_root)
            except ValueError:
                self.fail(
                    f"_index_path({vault}) = {idx} is not under "
                    f"{expected_root}. The index must be device-local."
                )

    def test_queue_path_stays_in_vault(self) -> None:
        """_queue_path(vault) must stay inside the vault (JSONL is safe to sync)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            q = vec_index._queue_path(vault)
            try:
                q.relative_to(vault)
            except ValueError:
                self.fail(
                    f"_queue_path({vault}) = {q} is outside the vault. "
                    "The embedding queue should remain vault-local."
                )

    def test_two_vaults_have_distinct_index_paths(self) -> None:
        """Different vault paths must produce different local index dirs."""
        with tempfile.TemporaryDirectory() as tmp:
            v1 = Path(tmp) / "VaultA"
            v2 = Path(tmp) / "VaultB"
            v1.mkdir()
            v2.mkdir()
            self.assertNotEqual(
                vec_index._index_path(v1),
                vec_index._index_path(v2),
                "Two different vaults must not share an index path.",
            )

    def test_index_filename(self) -> None:
        """The index file must be named vec-index.db."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            self.assertEqual(vec_index._index_path(vault).name, "vec-index.db")

    def test_rebuild_index_skipped_without_sqlite_vec(self) -> None:
        """rebuild_index returns skipped dict when sqlite-vec unavailable.

        This confirms the function executes without creating a vault-local DB.
        Even when skipped, no vec-index.db should appear in the vault.
        """
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "TestVault"
            vault.mkdir()
            result = vec_index.rebuild_index(vault)
            # If sqlite-vec is missing (common in CI), the result is skipped.
            # If sqlite-vec IS present, result has new_dim / entries_dropped.
            # Either way, the vault must NOT contain a vec-index.db.
            vault_db = vault / "_meta" / "vec-index.db"
            self.assertFalse(
                vault_db.exists(),
                f"rebuild_index created vec-index.db inside the vault at "
                f"{vault_db}. The index must be device-local only.",
            )


if __name__ == "__main__":
    unittest.main()
