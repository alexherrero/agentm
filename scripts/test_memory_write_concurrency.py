#!/usr/bin/env python3
"""Concurrency + atomic-write coverage for /memory save + evolve (V5-0 task 3b).

These two scripts live in `harness/skills/memory/scripts/` and are self-contained
by construction — the memory hooks resolve them across three install scopes and
top-level `scripts/` is not on a target's sys.path, so they import a *vendored*
byte-identical `vault_lock.py` sibling (DC-9; byte-identity enforced by
`scripts/check-vault-lock-parity.sh`). This test proves the routing works: every
vault write in save.py / evolve.py now goes through `vault_mutex` + `atomic_write`
(temp→fsync→rename, bytes-mode LF), leaves no `.tmp` remnant, and survives N
concurrent writers without a torn file or a deadlock.

Mirrors the engine-level proof in test_harness_memory.py::TestEngineConcurrencyProof
(task 3), one layer up at the /memory script surface.

All lock activity is redirected to a temp `XDG_CACHE_HOME` so the real
`~/.cache/agentm/locks/` is never touched (R4 rule 1 + the test-hygiene risk
pinned in PLAN.md).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# Import the modules under test from the memory skill scripts dir (the same
# cross-dir pattern test_vault_lint.py uses). save.py / evolve.py exist ONLY
# there, so `import save` / `import evolve` resolve unambiguously; their
# `from vault_lock import …` resolves to the co-located vendored copy.
_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import evolve  # noqa: E402
import save  # noqa: E402


def _tmp_remnants(root: Path) -> list[Path]:
    """Every leftover atomic-write temp file under `root` (should be none)."""
    return list(root.rglob("*.tmp"))


class _SpyMutex:
    """Context-manager stand-in for vault_mutex that records enter/exit + the
    vault path, but does NOT take a real lock — so the wrapped atomic_write
    still runs for real and the file lands. Used to prove the write site is
    wrapped in the mutex (and, for evolve, that it's ONE acquisition)."""

    events: list[tuple[str, str]] = []

    def __init__(self, vault_path, **_kw) -> None:
        self.vault_path = str(vault_path)
        _SpyMutex.events.append(("init", self.vault_path))

    def __enter__(self):
        _SpyMutex.events.append(("enter", self.vault_path))
        return self

    def __exit__(self, *_exc) -> bool:
        _SpyMutex.events.append(("exit", self.vault_path))
        return False


class _MemWriteTestBase(unittest.TestCase):
    """Shared setup: a temp vault + a temp lock root (XDG_CACHE_HOME) so no
    test ever writes the real ~/.cache."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        # Redirect the lock root away from the real ~/.cache for the whole test.
        self._prev_xdg = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(self.root / "cache")

    def tearDown(self) -> None:
        if self._prev_xdg is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._prev_xdg
        self._tmp.cleanup()


class TestSaveRouting(_MemWriteTestBase):
    def test_save_entry_no_tmp_remnant_and_content_exact(self) -> None:
        target = save.save_entry(
            self.vault, "note", "alpha", "first body line\nsecond line",
            group="personal", tags=["x", "y"],
        )
        self.assertTrue(target.is_file())
        self.assertEqual(_tmp_remnants(self.vault), [], "atomic_write left a .tmp behind")
        text = target.read_text(encoding="utf-8")
        self.assertIn("slug: alpha", text)
        self.assertIn("first body line", text)

    def test_save_entry_lf_preserved_no_crlf(self) -> None:
        target = save.save_entry(
            self.vault, "note", "lf-check", "line one\nline two\nline three",
            group="personal",
        )
        raw = target.read_bytes()
        self.assertNotIn(b"\r", raw, "atomic_write must keep LF-only (no CRLF) — V4 Windows-CI fix")

    def test_save_entry_is_wrapped_in_vault_mutex(self) -> None:
        _SpyMutex.events = []
        orig = save.vault_mutex
        save.vault_mutex = _SpyMutex
        try:
            target = save.save_entry(
                self.vault, "note", "mutexed", "body", group="personal",
            )
        finally:
            save.vault_mutex = orig
        self.assertTrue(target.is_file(), "file must still land while the mutex is spied")
        enters = [e for e in _SpyMutex.events if e[0] == "enter"]
        self.assertEqual(len(enters), 1, "save_entry must acquire the vault mutex exactly once")
        self.assertEqual(enters[0][1], str(self.vault))


class TestEvolveRouting(_MemWriteTestBase):
    def _seed(self, slug: str = "evolvable") -> Path:
        return save.save_entry(
            self.vault, "note", slug, "original body",
            group="personal", tags=["t"],
        )

    def test_evolve_in_place_no_tmp_remnant(self) -> None:
        self._seed()
        old_rel = Path("personal") / "note" / "evolvable.md"
        new_path, archive_path = evolve.evolve_entry(
            self.vault, old_rel, "updated body", "because reasons",
        )
        self.assertTrue(new_path.is_file())
        self.assertTrue(archive_path.is_file(), "archive must be written")
        self.assertEqual(_tmp_remnants(self.vault), [], "evolve left a .tmp behind")
        self.assertIn("updated body", new_path.read_text(encoding="utf-8"))
        self.assertIn("original body", archive_path.read_text(encoding="utf-8"))

    def test_evolve_lf_preserved_no_crlf(self) -> None:
        self._seed("lf-evolve")
        old_rel = Path("personal") / "note" / "lf-evolve.md"
        new_path, archive_path = evolve.evolve_entry(
            self.vault, old_rel, "new\nmulti\nline", "reason",
        )
        self.assertNotIn(b"\r", new_path.read_bytes())
        self.assertNotIn(b"\r", archive_path.read_bytes())

    def test_evolve_uses_single_mutex_acquisition(self) -> None:
        # The archive write + the new-entry write must happen under ONE lock
        # acquisition so no concurrent writer interleaves between them.
        self._seed("single-lock")
        old_rel = Path("personal") / "note" / "single-lock.md"
        _SpyMutex.events = []
        orig = evolve.vault_mutex
        evolve.vault_mutex = _SpyMutex
        try:
            new_path, archive_path = evolve.evolve_entry(
                self.vault, old_rel, "v2", "reason",
            )
        finally:
            evolve.vault_mutex = orig
        enters = [e for e in _SpyMutex.events if e[0] == "enter"]
        self.assertEqual(len(enters), 1, "evolve must wrap archive+new in a single mutex acquisition")
        self.assertTrue(new_path.is_file() and archive_path.is_file())

    def test_evolve_rename_writes_new_and_unlinks_old(self) -> None:
        self._seed("rename-me")
        old_rel = Path("personal") / "note" / "rename-me.md"
        new_path, archive_path = evolve.evolve_entry(
            self.vault, old_rel, "renamed body", "reason", new_slug="renamed",
        )
        self.assertEqual(new_path.name, "renamed.md")
        self.assertTrue(new_path.is_file())
        self.assertFalse((self.vault / old_rel).exists(), "old path must be unlinked on rename")
        self.assertEqual(_tmp_remnants(self.vault), [])


class TestConcurrentSaves(_MemWriteTestBase):
    def test_concurrent_distinct_slug_saves_all_land_no_tmp(self) -> None:
        # The realistic partitioned case (DC-2): N agents each save a DISTINCT
        # slug against one shared vault concurrently. They contend on the single
        # per-vault mutex (serialized acquisition) but write disjoint files.
        # Proves: no deadlock, every file lands well-formed, zero .tmp remnants.
        n_writers = 8
        barrier = threading.Barrier(n_writers)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                barrier.wait(timeout=10)  # release all writers together → real contention
                save.save_entry(
                    self.vault, "note", f"slug-{i:02d}", f"body for {i}",
                    group="personal",
                )
            except BaseException as exc:  # noqa: BLE001 — capture for the assert
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(any(t.is_alive() for t in threads), "a writer hung (possible deadlock)")
        self.assertEqual(errors, [], f"concurrent saves raised: {errors}")
        note_dir = self.vault / "personal" / "note"
        landed = sorted(p.name for p in note_dir.glob("*.md"))
        self.assertEqual(landed, [f"slug-{i:02d}.md" for i in range(n_writers)])
        self.assertEqual(_tmp_remnants(self.vault), [], "a .tmp survived the concurrent run")
        # Each file is intact — its own body, no cross-contamination from a torn write.
        for i in range(n_writers):
            text = (note_dir / f"slug-{i:02d}.md").read_text(encoding="utf-8")
            self.assertIn(f"body for {i}", text)


if __name__ == "__main__":
    unittest.main()
