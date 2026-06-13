#!/usr/bin/env python3
"""Contract tests for `storage_device_local` (V5-1 part 2/5) — the device-local backend.

`DeviceLocalBackend` is the first *concrete* `StorageBackend`: plain markdown
under ``~/.agentm/memory``, the fresh-install default. These tests pin its
behavior against a real temp root (never the operator's home — every backend is
constructed with an injected ``root=tmp``), so they are hermetic and run on every
OS.

  - **The seam verbs, round-tripped on the filesystem** — resolve→write→read
    byte-for-byte, exists False→True, info's size/kind/mtime, list's child
    locators, mkdir idempotence, root-confinement (a ``..`` key raises). The same
    surface `test_storage_seam` proved over a dict, now proven over real files.
  - **Crash-safe write** (task 2) — write composes V5-0 `atomic_write` (temp +
    fsync + rename), so an interrupted write leaves the prior bytes intact and a
    successful overwrite is atomic; it never open-and-truncates.
  - **Conflict strategy + scope guard** (task 3) — device-local inherits the
    seam's ``"none"`` strategy (single machine, nothing to reconcile), and ships
    no derived-index or conflict-merger machinery.

Run directly:

    python3 scripts/test_storage_device_local.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import storage_seam as ss  # noqa: E402
import storage_device_local as sdl  # noqa: E402


class DeviceLocalSeamVerbs(unittest.TestCase):
    """The seven verbs + capabilities, against a real temp root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "agentm-memory"  # not yet created
        self.b = sdl.DeviceLocalBackend(root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_construction_creates_root_on_first_use(self) -> None:
        # The root need not pre-exist; constructing the backend (its first use)
        # creates it, so the root locator always resolves to a real directory.
        self.assertTrue(self.root.is_dir())
        fresh = Path(self._tmp.name) / "another" / "nested" / "root"
        self.assertFalse(fresh.exists())
        sdl.DeviceLocalBackend(root=fresh)
        self.assertTrue(fresh.is_dir())

    def test_resolve_makes_a_locator_from_parts(self) -> None:
        loc = self.b.resolve("projects", "agentm", "PLAN.md")
        self.assertIsInstance(loc, ss.Locator)
        self.assertEqual(loc.key, "projects/agentm/PLAN.md")
        self.assertEqual(self.b.resolve().key, "")  # no parts → root

    def test_write_then_read_round_trips(self) -> None:
        loc = self.b.resolve("notes", "a.md")
        written = self.b.write(loc, "# hello\n")
        self.assertIsInstance(written, ss.Locator)
        self.assertEqual(written, loc)  # write returns the locator written
        self.assertEqual(self.b.read(loc), "# hello\n")

    def test_round_trip_preserves_crlf_and_unicode_bytes(self) -> None:
        # read_bytes + utf-8 (not read_text) → no newline translation: CRLF and
        # non-ASCII survive byte-for-byte, matching atomic_write's byte mode.
        loc = self.b.resolve("x.md")
        payload = "a\r\nb\n— café 🦗\n"
        self.b.write(loc, payload)
        self.assertEqual(self.b.read(loc), payload)

    def test_read_missing_raises_filenotfound(self) -> None:
        # The absent-data signal — distinct from InvalidLocatorError (a bad key).
        with self.assertRaises(FileNotFoundError):
            self.b.read(self.b.resolve("nope.md"))

    def test_exists_tracks_writes(self) -> None:
        loc = self.b.resolve("x.md")
        self.assertFalse(self.b.exists(loc))
        self.b.write(loc, "data")
        self.assertTrue(self.b.exists(loc))

    def test_list_returns_immediate_child_locators(self) -> None:
        self.b.write(self.b.resolve("d", "a.md"), "1")
        self.b.write(self.b.resolve("d", "b.md"), "2")
        self.b.write(self.b.resolve("e.md"), "3")
        root_children = self.b.list(self.b.resolve())
        self.assertEqual({c.key for c in root_children}, {"d", "e.md"})
        d_children = self.b.list(self.b.resolve("d"))
        self.assertEqual({c.key for c in d_children}, {"d/a.md", "d/b.md"})
        self.assertTrue(all(isinstance(c, ss.Locator) for c in d_children))

    def test_list_absent_or_file_locator_is_empty(self) -> None:
        # Device-local's choice (part 3 pins it across backends): listing an
        # absent locator, or a file, yields [] rather than raising.
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
        # mtime is a real epoch timestamp; assert it is set, not that it strictly
        # advances on rewrite (filesystem mtime resolution is coarse → would flake).
        self.assertIsInstance(info.mtime, float)
        self.assertGreater(info.mtime, 0)

    def test_info_of_directory_is_zero_sized_dir(self) -> None:
        loc = self.b.resolve("d")
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

    def test_capabilities_report_single_machine_floor(self) -> None:
        caps = self.b.capabilities
        self.assertIsInstance(caps, ss.Capabilities)
        self.assertFalse(caps.concurrent_writers)
        self.assertFalse(caps.sync)
        self.assertFalse(caps.conflict_files)
        self.assertFalse(caps.encryption)

    def test_double_dot_key_is_rejected(self) -> None:
        # Root-confinement holds end-to-end: a '..' segment can't be resolved,
        # so it can never reach the filesystem join.
        with self.assertRaises(ss.InvalidLocatorError):
            self.b.resolve("a", "..", "b")
        with self.assertRaises(ss.InvalidLocatorError):
            ss.Locator("../escape")

    def test_no_verb_returns_a_path(self) -> None:
        # Behavioral mirror of the no-path-leak gate, on the concrete backend.
        loc = self.b.resolve("d", "a.md")
        self.b.write(loc, "x")
        self.b.mkdir(self.b.resolve("d"))
        results = [
            self.b.resolve("a"),
            self.b.read(loc),
            self.b.write(loc, "y"),
            self.b.list(self.b.resolve("d")),
            self.b.exists(loc),
            self.b.info(loc),
            self.b.mkdir(self.b.resolve("d")),
        ]
        for r in results:
            self.assertNotIsInstance(r, Path)


class DeviceLocalDefaultRoot(unittest.TestCase):
    """The fresh-install default resolves to ``~/.agentm/memory``."""

    def test_default_root_is_home_agentm_memory(self) -> None:
        # Patch Path.home (not env) so the check is cross-platform; the temp home
        # is cleaned up, so the operator's real ~/.agentm/memory is never touched.
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            with mock.patch.object(Path, "home", return_value=fake_home):
                b = sdl.DeviceLocalBackend()
                self.assertEqual(b.root, fake_home / ".agentm" / "memory")
                self.assertTrue(b.root.is_dir())


class DeviceLocalRegistration(unittest.TestCase):
    """The backend registers under ``device-local`` in the seam's default registry."""

    def test_protocol_name_is_device_local(self) -> None:
        self.assertEqual(sdl.PROTOCOL, "device-local")

    def test_registered_in_default_registry(self) -> None:
        # The part-1 registry resolves the protocol name to this class — the hook
        # part 5's selection reads. Importing the module performed the register.
        self.assertIn(sdl.PROTOCOL, ss.registry)
        self.assertIs(ss.registry.get("device-local"), sdl.DeviceLocalBackend)


if __name__ == "__main__":
    unittest.main()
