#!/usr/bin/env python3
"""Contract + composition tests for `storage_vault` (V5-1 part 4/5) — the vault backend.

`VaultBackend` is the **transitional** wrap-and-move of today's Obsidian/GDrive
vault behind the part-1 seam. These tests pin its behavior against a real *scratch*
temp root (never the operator's live vault — every backend is constructed with an
injected ``root=tmp`` **and** an injected ``lock_root=tmp`` so the real
``~/.cache`` lock base is never touched either), so they are hermetic and run on
every OS.

  - **The seam verbs, round-tripped on the filesystem** — resolve→write→read
    byte-for-byte (incl. CRLF + non-ASCII), exists False→True, info's
    size/kind/mtime, list's child locators, mkdir idempotence, root-confinement.
    The same surface device-local proved, now over the vault wrap.
  - **The FULL V5-0 stack on the write path** (task 1's headline) — unlike
    device-local's ``atomic_write``-only write, the vault composes ``vault_mutex``
    + content-hash CAS + ``atomic_write``. These prove all three are *invoked, not
    bypassed*: the mutex is entered with the vault root, the write routes through
    the CAS with the prior-content hash, the CAS *bites* on a concurrent
    modification, and the land is the crash-safe ``atomic_write`` (torn-write
    leaves prior bytes intact).
  - **The synced, multi-writer capability profile** — ``concurrent_writers`` /
    ``sync`` / ``conflict_files`` True (the positive contrast to device-local's
    floor); ``encryption`` False.

Run directly:

    python3 scripts/test_storage_vault.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402
import storage_device_local as sdl  # noqa: E402
import storage_seam as ss  # noqa: E402
import storage_vault as sv  # noqa: E402
from vault_lock import ConcurrentModificationError, content_hash  # noqa: E402


class _ScratchVaultMixin:
    """A fresh scratch vault root + injected local lock base for each test.

    Both the vault root and the mutex lock base are throwaway temp dirs, so no
    test ever touches the operator's live vault nor the real ``~/.cache`` locks.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "vault" / "projects" / "agentm"  # not yet created
        self.lock_root = base / "locks"
        self.b = sv.VaultBackend(root=self.root, lock_root=self.lock_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class VaultSeamVerbs(_ScratchVaultMixin, unittest.TestCase):
    """The seven verbs + capabilities, against a real scratch vault root."""

    def test_construction_creates_root_on_first_use(self) -> None:
        self.assertTrue(self.root.is_dir())
        fresh = Path(self._tmp.name) / "another" / "nested" / "root"
        self.assertFalse(fresh.exists())
        sv.VaultBackend(root=fresh, lock_root=self.lock_root)
        self.assertTrue(fresh.is_dir())

    def test_root_is_required(self) -> None:
        # Unlike device-local (fixed ~/.agentm/memory), the vault has no universal
        # default root — it is resolved per-project — so root is a required arg.
        with self.assertRaises(TypeError):
            sv.VaultBackend()  # type: ignore[call-arg]

    def test_resolve_makes_a_locator_from_parts(self) -> None:
        loc = self.b.resolve("_harness", "PLAN.md")
        self.assertIsInstance(loc, ss.Locator)
        self.assertEqual(loc.key, "_harness/PLAN.md")
        self.assertEqual(self.b.resolve().key, "")  # no parts → root

    def test_write_then_read_round_trips(self) -> None:
        loc = self.b.resolve("_harness", "PLAN.md")
        written = self.b.write(loc, "# plan\n")
        self.assertIsInstance(written, ss.Locator)
        self.assertEqual(written, loc)
        self.assertEqual(self.b.read(loc), "# plan\n")

    def test_round_trip_preserves_crlf_and_unicode_bytes(self) -> None:
        # read_bytes + utf-8 (not read_text) → no newline translation: CRLF and
        # non-ASCII survive byte-for-byte, the LF-exact discipline the Windows
        # runner proves in the conformance suite (task 2).
        loc = self.b.resolve("x.md")
        payload = "a\r\nb\n— café 🦗\n"
        self.b.write(loc, payload)
        self.assertEqual(self.b.read(loc), payload)

    def test_read_missing_raises_filenotfound(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.b.read(self.b.resolve("nope.md"))

    def test_exists_tracks_writes(self) -> None:
        loc = self.b.resolve("x.md")
        self.assertFalse(self.b.exists(loc))
        self.b.write(loc, "data")
        self.assertTrue(self.b.exists(loc))

    def test_list_returns_immediate_child_locators(self) -> None:
        self.b.write(self.b.resolve("_harness", "PLAN.md"), "1")
        self.b.write(self.b.resolve("_harness", "progress.md"), "2")
        self.b.write(self.b.resolve("e.md"), "3")
        root_children = self.b.list(self.b.resolve())
        self.assertEqual({c.key for c in root_children}, {"_harness", "e.md"})
        h_children = self.b.list(self.b.resolve("_harness"))
        self.assertEqual(
            {c.key for c in h_children}, {"_harness/PLAN.md", "_harness/progress.md"}
        )
        self.assertTrue(all(isinstance(c, ss.Locator) for c in h_children))

    def test_list_absent_or_file_locator_is_empty(self) -> None:
        self.assertEqual(self.b.list(self.b.resolve("ghostdir")), [])
        self.b.write(self.b.resolve("file.md"), "x")
        self.assertEqual(self.b.list(self.b.resolve("file.md")), [])

    def test_info_reports_size_kind_and_mtime(self) -> None:
        loc = self.b.resolve("a.md")
        self.b.write(loc, "abcde")
        info = self.b.info(loc)
        self.assertIsInstance(info, ss.Info)
        self.assertFalse(info.is_dir)
        self.assertEqual(info.size, 5)
        self.assertEqual(info.locator, loc)
        self.assertIsInstance(info.mtime, float)
        self.assertGreater(info.mtime, 0)

    def test_info_of_directory_is_zero_sized_dir(self) -> None:
        loc = self.b.resolve("_harness")
        self.b.mkdir(loc)
        info = self.b.info(loc)
        self.assertTrue(info.is_dir)
        self.assertEqual(info.size, 0)

    def test_info_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.b.info(self.b.resolve("ghost"))

    def test_mkdir_is_idempotent_and_marks_a_dir(self) -> None:
        loc = self.b.resolve("newdir")
        made = self.b.mkdir(loc)
        self.assertIsInstance(made, ss.Locator)
        self.assertTrue(self.b.exists(loc))
        self.assertTrue(self.b.info(loc).is_dir)
        self.assertEqual(self.b.mkdir(loc), loc)  # idempotent — no raise

    def test_mkdir_creates_intermediate_dirs(self) -> None:
        loc = self.b.resolve("a", "b", "c")
        self.b.mkdir(loc)
        self.assertTrue(self.b.exists(self.b.resolve("a", "b")))
        self.assertTrue(self.b.info(loc).is_dir)

    def test_double_dot_key_is_rejected(self) -> None:
        with self.assertRaises(ss.InvalidLocatorError):
            self.b.resolve("a", "..", "b")
        with self.assertRaises(ss.InvalidLocatorError):
            ss.Locator("../escape")

    def test_no_verb_returns_a_path(self) -> None:
        # Behavioral mirror of the no-path-leak gate, on the concrete backend.
        loc = self.b.resolve("_harness", "PLAN.md")
        self.b.write(loc, "x")
        self.b.mkdir(self.b.resolve("_harness"))
        results = [
            self.b.resolve("a"),
            self.b.read(loc),
            self.b.write(loc, "y"),
            self.b.list(self.b.resolve("_harness")),
            self.b.exists(loc),
            self.b.info(loc),
            self.b.mkdir(self.b.resolve("_harness")),
        ]
        for r in results:
            self.assertNotIsInstance(r, Path)


class VaultCapabilities(_ScratchVaultMixin, unittest.TestCase):
    """The synced, multi-writer profile — the positive contrast to device-local."""

    def test_capabilities_report_synced_multi_writer(self) -> None:
        caps = self.b.capabilities
        self.assertIsInstance(caps, ss.Capabilities)
        self.assertTrue(caps.concurrent_writers)  # the mutex makes N writers safe
        self.assertTrue(caps.sync)  # GDrive replicates the tree
        self.assertTrue(caps.conflict_files)  # DriveFS surfaces conflict copies
        self.assertFalse(caps.encryption)  # not encrypted at rest by the backend


class VaultConflictStrategy(_ScratchVaultMixin, unittest.TestCase):
    """The conflict-strategy override — the vault names the GDrive whole-file policy.

    The slot shipped in part 2 (a concrete ``@property`` on the contract
    defaulting to ``"none"``); part 4 task 3 is the *override*. Proven here: the
    vault returns ``"whole-file"``; device-local still inherits ``"none"`` (the
    policy is per-backend, not a global change); and the slot is observable with
    no ``hasattr`` dance — a plain property on the contract, never an
    abstractmethod (the part-2 ``ConflictStrategySlot`` pin, re-asserted off the
    vault).
    """

    def test_vault_conflict_strategy_is_whole_file(self) -> None:
        self.assertEqual(self.b.conflict_strategy, "whole-file")

    def test_conflict_strategy_is_a_str(self) -> None:
        # A name the part-5 selection switches on — not a bool or a path.
        self.assertIsInstance(self.b.conflict_strategy, str)

    def test_device_local_still_inherits_none(self) -> None:
        # The override is per-backend: device-local (single machine, nothing to
        # reconcile) is unaffected and still answers the inherited "none" floor.
        dl = sdl.DeviceLocalBackend(root=Path(self._tmp.name) / "dl-memory")
        self.assertEqual(dl.conflict_strategy, "none")

    def test_slot_is_observable_with_no_hasattr_dance(self) -> None:
        # The contract always answers conflict_strategy — a concrete property,
        # not an abstractmethod — so a caller reads it off any backend with no
        # hasattr/getattr guard. Asserted both on the base (not abstract) and on
        # the vault (the override is itself a plain property).
        self.assertNotIn("conflict_strategy", ss.StorageBackend.__abstractmethods__)
        self.assertIsInstance(type(self.b).conflict_strategy, property)


class VaultRegistration(unittest.TestCase):
    """The backend registers under ``vault`` in the seam's default registry."""

    def test_protocol_name_is_vault(self) -> None:
        self.assertEqual(sv.PROTOCOL, "vault")

    def test_registered_in_default_registry(self) -> None:
        self.assertIn(sv.PROTOCOL, ss.registry)
        self.assertIs(ss.registry.get("vault"), sv.VaultBackend)


class VaultWriteComposesFullV50Stack(_ScratchVaultMixin, unittest.TestCase):
    """write composes the FULL V5-0 stack — vault_mutex + CAS + atomic_write.

    The composition proof (task 1's verification): the three primitives are
    *invoked, not bypassed* — the load-bearing difference from device-local, whose
    write composes ``atomic_write`` only.
    """

    def test_write_acquires_the_vault_mutex_on_the_vault_root(self) -> None:
        # The mutex is entered, keyed on the vault root, with the injected
        # lock_root — proving fleet-local writes are serialized, not bypassed.
        loc = self.b.resolve("_harness", "PLAN.md")
        with mock.patch("storage_vault.vault_mutex") as vm:
            self.b.write(loc, "body")
        vm.assert_called_once_with(self.root, lock_root=self.lock_root)
        # The returned context manager was entered/exited (the `with` ran).
        vm.return_value.__enter__.assert_called_once()
        vm.return_value.__exit__.assert_called_once()

    def test_write_composes_atomic_write_under_the_mutex(self) -> None:
        # The land is vault_lock.atomic_write (temp + fsync + rename), composed
        # under the (real) mutex — never an open-and-truncate.
        loc = self.b.resolve("_harness", "PLAN.md")
        with mock.patch("storage_vault.atomic_write") as aw:
            returned = self.b.write(loc, "body")
        aw.assert_called_once()
        called_path, called_content = aw.call_args.args[0], aw.call_args.args[1]
        self.assertEqual(Path(called_path), self.root / "_harness" / "PLAN.md")
        self.assertEqual(called_content, "body")
        self.assertEqual(returned, loc)

    def test_write_routes_through_cas_with_the_prior_content_hash(self) -> None:
        # On an existing target, write captures the prior bytes' hash and hands it
        # to the CAS as `expected_hash`; on a fresh target there is no prior hash.
        loc = self.b.resolve("a.md")
        with mock.patch.object(self.b, "_cas_atomic_write") as cas:
            self.b.write(loc, "first")
        self.assertIsNone(cas.call_args.kwargs["expected_hash"])  # fresh → None

        self.b.write(loc, "first")  # now the file exists
        with mock.patch.object(self.b, "_cas_atomic_write") as cas:
            self.b.write(loc, "second")
        self.assertEqual(
            cas.call_args.kwargs["expected_hash"], content_hash(b"first")
        )

    def test_cas_rejects_a_concurrent_modification(self) -> None:
        # The CAS *bites*: a stale expected_hash (the on-disk bytes changed since
        # the pre-write read — a non-mutex Drive-sync writer) raises rather than
        # silently clobbering. This is the guard device-local does not have.
        loc = self.b.resolve("a.md")
        self.b.write(loc, "real-current-content")
        target = self.b._path(loc)
        with self.assertRaises(ConcurrentModificationError):
            self.b._cas_atomic_write(
                target, "my-overwrite", expected_hash=content_hash(b"a-stale-read")
            )
        # And it did NOT clobber — the real current content survives intact.
        self.assertEqual(self.b.read(loc), "real-current-content")

    def test_cas_with_no_prior_hash_writes_unconditionally(self) -> None:
        # expected_hash=None (a fresh file) → plain atomic land, no CAS check.
        loc = self.b.resolve("new.md")
        target = self.b._path(loc)
        self.b._cas_atomic_write(target, "fresh", expected_hash=None)
        self.assertEqual(self.b.read(loc), "fresh")

    def test_concurrent_write_in_window_is_caught_end_to_end(self) -> None:
        # End-to-end CAS bite through the public `write` verb. The write path reads
        # the target twice under the mutex: once to capture `expected` (write), once
        # to re-check in the CAS (_cas_atomic_write). Simulate a non-mutex writer
        # (Drive sync) landing "v2-foreign" *between* those two reads: the re-read
        # then sees v2-foreign ≠ expected=hash(v1) → ConcurrentModificationError,
        # so our blind "v3-mine" overwrite is refused and the foreign write survives.
        loc = self.b.resolve("a.md")
        self.b.write(loc, "v1")
        target = self.b._path(loc)
        real_read_bytes = Path.read_bytes
        reads = {"n": 0}

        def _foreign_write_between_reads(self_path: Path):
            data = real_read_bytes(self_path)
            if self_path == target:
                reads["n"] += 1
                if reads["n"] == 1:  # after the pre-write read, before the CAS re-read
                    target.write_bytes(b"v2-foreign")
            return data

        with mock.patch.object(Path, "read_bytes", _foreign_write_between_reads):
            with self.assertRaises(ConcurrentModificationError):
                self.b.write(loc, "v3-mine")
        self.assertEqual(self.b.read(loc), "v2-foreign")

    def test_torn_write_leaves_prior_bytes_intact(self) -> None:
        # Crash between temp-stage and rename: the target keeps its PRIOR bytes,
        # never a truncated/partial file. Inject the failure at os.replace.
        loc = self.b.resolve("a.md")
        self.b.write(loc, "v1-good")
        with mock.patch("vault_lock.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                self.b.write(loc, "v2-torn")
        self.assertEqual(self.b.read(loc), "v1-good")

    def test_successful_overwrite_replaces_atomically(self) -> None:
        loc = self.b.resolve("a.md")
        self.b.write(loc, "v1")
        self.b.write(loc, "v2-longer-content")
        self.assertEqual(self.b.read(loc), "v2-longer-content")


class VaultNeverOrphanInvariant(unittest.TestCase):
    """The expand-step invariant — the wrap moves no data (task 4's headline).

    The transitional vault wrap must reach the *exact same bytes at the exact
    same path* the engine's old direct vault write/read reach — that is the whole
    safety claim of "wrap, not migrate": expanding the seam in front of the live
    vault orphans nothing, on either side of the cut-over. Proven both directions
    over a *scratch* vault (never the operator's live vault):

      A. the old way writes (``harness_memory.write_state_file``'s vault branch) →
         the seam reads it back identical (``VaultBackend.read``) — a session that
         has moved to the seam sees an already-present vault file unchanged;
      B. the seam writes (``VaultBackend.write``, the full V5-0 stack) → the old
         way reads it back identical (``harness_memory.read_state_file``'s vault
         branch) — a session still on the old read path sees a seam-written file
         unchanged.

    Plus the crux of "no data moves": both name the **same on-disk file**
    (``_harness/<name>`` under ``<vault>/projects/<slug>``), so there is one set
    of bytes reached two ways — not a migrated copy.

    Hermeticity: the vault branch is forced deterministically with a per-repo
    ``.harness/.project-mode = "vault"`` marker — the authoritative layer-1
    override (``_read_project_mode``), so the run never consults, and never
    depends on, the operator's device config — and ``XDG_CACHE_HOME`` is
    redirected into the scratch tree so the engine's ``vault_mutex`` (whose lock
    base is *not* injectable on the ``write_state_file`` path) lands its lockdir
    in temp, never the real ``~/.cache``.
    """

    # Representative harness state-file content: LF newlines (every real PLAN.md /
    # progress.md is LF) plus non-ASCII. Deliberately NOT CRLF — the engine's
    # read_state_file decodes via read_text (universal-newline translating), while
    # the seam's read is byte-exact; they agree on exactly the LF content the
    # harness actually stores. (The CRLF byte-exact round-trip is each backend's
    # own conformance/round-trip case, not the cross-path never-orphan proof.)
    PAYLOAD = "# PLAN\n\n- [x] task 1 — café\n- [ ] task 4 🦗\n"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        # Scratch vault shaped like the real per-project path.
        self.vault_p = base / "vault" / "projects" / "agentm"
        # A project root carrying the authoritative per-repo "vault" marker, so
        # _read_project_mode resolves vault-mode without reading device config.
        self.project_root = base / "project"
        (self.project_root / ".harness").mkdir(parents=True)
        (self.project_root / ".harness" / ".project-mode").write_text(
            "vault", encoding="utf-8"
        )
        self.resolution = {
            "vault_path": self.vault_p,
            "project_root": self.project_root,
        }
        # Redirect the engine's vault_mutex lock base into the scratch tree — the
        # write_state_file path acquires the mutex with no injectable lock_root,
        # so XDG_CACHE_HOME is the escape valve that keeps the real ~/.cache untouched.
        self._xdg = mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": str(base / "cache")}
        )
        self._xdg.start()
        self.addCleanup(self._xdg.stop)
        # The seam backend over the *same* scratch vault, with its own injected
        # lock base (belt-and-suspenders: never the real ~/.cache either way).
        self.backend = sv.VaultBackend(root=self.vault_p, lock_root=base / "seam-locks")

    def test_old_way_write_is_read_identically_through_the_seam(self) -> None:
        # Direction A: the engine's vault write lands bytes; the seam reads them
        # back unchanged. Expanding the seam in front of an existing vault file
        # orphans nothing.
        written = hm.write_state_file(self.resolution, "PLAN.md", self.PAYLOAD)
        self.assertEqual(written, self.vault_p / "_harness" / "PLAN.md")
        seam_read = self.backend.read(self.backend.resolve("_harness", "PLAN.md"))
        self.assertEqual(seam_read, self.PAYLOAD)

    def test_seam_write_is_read_identically_the_old_way(self) -> None:
        # Direction B: the seam writes (full V5-0 stack); the engine's old read
        # path returns the same content.
        self.backend.write(self.backend.resolve("_harness", "progress.md"), self.PAYLOAD)
        old_read = hm.read_state_file(self.resolution, "progress.md")
        self.assertEqual(old_read, self.PAYLOAD)

    def test_both_paths_name_the_identical_file(self) -> None:
        # The crux of "no data moves": the old way and the seam resolve to the
        # SAME on-disk path — one set of bytes reached two ways, not a copy.
        # Proven structurally (paths equal) and behaviorally (an old-way rewrite
        # is visible through the seam read at that path).
        old_target = hm.write_state_file(self.resolution, "PLAN.md", self.PAYLOAD)
        seam_path = self.backend._path(self.backend.resolve("_harness", "PLAN.md"))
        self.assertEqual(old_target, seam_path)
        # One file, two doors: an old-way rewrite is seen by the seam read.
        hm.write_state_file(self.resolution, "PLAN.md", "rewritten\n")
        self.assertEqual(
            self.backend.read(self.backend.resolve("_harness", "PLAN.md")),
            "rewritten\n",
        )

    def test_round_trip_is_byte_exact_through_both_doors(self) -> None:
        # The full loop a cut-over actually exercises: old-way write → seam read →
        # seam write (same bytes) → old-way read, all returning the original.
        # If any door silently rewrote the bytes (newline translation, re-encode),
        # this would diverge.
        hm.write_state_file(self.resolution, "PLAN.md", self.PAYLOAD)
        loc = self.backend.resolve("_harness", "PLAN.md")
        via_seam = self.backend.read(loc)
        self.assertEqual(via_seam, self.PAYLOAD)
        self.backend.write(loc, via_seam)
        self.assertEqual(hm.read_state_file(self.resolution, "PLAN.md"), self.PAYLOAD)


if __name__ == "__main__":
    unittest.main()
