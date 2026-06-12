#!/usr/bin/env python3
"""Unit tests for scripts/vault_lock.py — stdlib unittest, cross-platform.

Run directly:

    python3 scripts/test_vault_lock.py

Covers (V5-0 task 1 verification):
  - atomic_write: byte-exact, LF preserved, no .tmp remnant, fsync invoked
  - content_hash: stability + str/bytes equivalence + collision-freedom
  - vault_mutex: two-thread mutual exclusion (no interleave)
  - stale-takeover after a forced-old heartbeat
  - LockTimeout on a held, non-stale lock within `timeout`
  - atexit + signal cleanup removes the lockdir
  - lockdir resolves under the injected lock_root, never under the vault
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_lock as vl  # noqa: E402


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="vault-lock-aw-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_str_content_byte_exact(self) -> None:
        target = self.dir / "note.md"
        ret = vl.atomic_write(target, "hello\nworld\n")
        self.assertEqual(ret, target)
        self.assertEqual(target.read_bytes(), b"hello\nworld\n")

    def test_bytes_content_verbatim(self) -> None:
        target = self.dir / "raw.bin"
        payload = b"\x00\x01\x02\xffabc\n"
        vl.atomic_write(target, payload)
        self.assertEqual(target.read_bytes(), payload)

    def test_lf_preserved_no_crlf_translation(self) -> None:
        target = self.dir / "lf.md"
        vl.atomic_write(target, "a\nb\nc\n")
        # Byte-for-byte LF — the centralized writer must not reintroduce the
        # write_text CRLF translation the V4 Windows-CI fix removed.
        self.assertNotIn(b"\r\n", target.read_bytes())
        self.assertEqual(target.read_bytes(), b"a\nb\nc\n")

    def test_no_tmp_remnant(self) -> None:
        target = self.dir / "x.md"
        vl.atomic_write(target, "content")
        self.assertFalse((self.dir / "x.md.tmp").exists())
        self.assertEqual(list(self.dir.iterdir()), [target])

    def test_creates_parent_dir(self) -> None:
        target = self.dir / "nested" / "deep" / "f.md"
        vl.atomic_write(target, "ok")
        self.assertTrue(target.exists())

    def test_overwrite_replaces_atomically(self) -> None:
        target = self.dir / "o.md"
        vl.atomic_write(target, "first")
        vl.atomic_write(target, "second")
        self.assertEqual(target.read_text(encoding="utf-8"), "second")
        self.assertFalse((self.dir / "o.md.tmp").exists())

    def test_fsync_invoked_by_default(self) -> None:
        target = self.dir / "s.md"
        with mock.patch.object(vl.os, "fsync") as spy:
            vl.atomic_write(target, "data")
        spy.assert_called_once()
        # fsync is called on an integer fd.
        self.assertIsInstance(spy.call_args[0][0], int)

    def test_fsync_skipped_when_disabled(self) -> None:
        target = self.dir / "n.md"
        with mock.patch.object(vl.os, "fsync") as spy:
            vl.atomic_write(target, "data", fsync=False)
        spy.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "data")


class ContentHashTests(unittest.TestCase):
    def test_stable(self) -> None:
        self.assertEqual(vl.content_hash("abc"), vl.content_hash("abc"))

    def test_str_bytes_equivalence(self) -> None:
        self.assertEqual(vl.content_hash("abc"), vl.content_hash(b"abc"))

    def test_distinct_inputs_distinct_hashes(self) -> None:
        self.assertNotEqual(vl.content_hash("abc"), vl.content_hash("abd"))

    def test_is_sha256_hex(self) -> None:
        h = vl.content_hash("abc")
        self.assertEqual(len(h), 64)
        int(h, 16)  # raises ValueError if not hex


class LockdirResolutionTests(unittest.TestCase):
    def test_lockdir_under_injected_root_never_under_vault(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="vault-lock-root-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        vault = Path(tempfile.mkdtemp(prefix="vault-lock-vault-"))
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)

        lockdir = vl._lockdir_for(vault, root)
        # Resolves under the injected root...
        self.assertEqual(Path(os.path.commonpath([lockdir, root])), root)
        # ...and the vault is never an ancestor of the lock (R4 rule 1).
        self.assertNotIn(Path(os.path.realpath(vault)), lockdir.parents)
        self.assertNotIn(str(vault), str(lockdir))
        # Pure computation: nothing created on disk yet.
        self.assertFalse(lockdir.exists())

    def test_default_root_is_dot_cache_agentm_locks(self) -> None:
        vault = "/some/vault/path"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_CACHE_HOME", None)
            lockdir = vl._lockdir_for(vault, None)
        expected_base = Path.home() / ".cache" / "agentm" / "locks"
        self.assertEqual(Path(os.path.commonpath([lockdir, expected_base])), expected_base)

    def test_xdg_cache_home_honored(self) -> None:
        vault = "/some/vault/path"
        fake_xdg = "/tmp/xdg-cache-test-vault-lock"
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": fake_xdg}):
            lockdir = vl._lockdir_for(vault, None)
        # Path-aware ancestor check: str.startswith breaks on Windows back-slash
        # separators, but the lockdir is rooted under XDG_CACHE_HOME on every
        # platform. Mirrors test_default_root_is_dot_cache_agentm_locks above.
        self.assertEqual(Path(os.path.commonpath([lockdir, Path(fake_xdg)])), Path(fake_xdg))

    def test_symlink_aliases_collide_on_same_lock(self) -> None:
        # Two paths pointing at the same physical vault must hash to one lock.
        real = Path(tempfile.mkdtemp(prefix="vault-lock-real-"))
        self.addCleanup(shutil.rmtree, real, ignore_errors=True)
        link = Path(tempfile.mkdtemp(prefix="vault-lock-ln-")) / "alias"
        self.addCleanup(shutil.rmtree, link.parent, ignore_errors=True)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        root = Path(tempfile.mkdtemp(prefix="vault-lock-root2-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertEqual(vl._lockdir_for(real, root), vl._lockdir_for(link, root))


class VaultMutexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="vault-lock-mtx-root-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.vault = Path(tempfile.mkdtemp(prefix="vault-lock-mtx-vault-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def test_mutual_exclusion_no_interleave(self) -> None:
        events: list[tuple[str, int]] = []

        def worker(tid: int) -> None:
            with vl.vault_mutex(self.vault, lock_root=self.root, timeout=10.0):
                events.append(("enter", tid))
                time.sleep(0.05)
                events.append(("exit", tid))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(events), 8)
        # If the mutex serializes, each thread's enter is immediately followed
        # by its own exit — no two enters in a row.
        for i in range(0, len(events), 2):
            self.assertEqual(events[i][0], "enter", f"interleave at {events}")
            self.assertEqual(events[i + 1][0], "exit", f"interleave at {events}")
            self.assertEqual(events[i][1], events[i + 1][1], f"interleave at {events}")

    def test_lock_released_after_block(self) -> None:
        # Sequential acquisitions succeed (release actually frees the lock).
        for _ in range(3):
            with vl.vault_mutex(self.vault, lock_root=self.root, timeout=5.0):
                pass
        lockdir = vl._lockdir_for(self.vault, self.root)
        self.assertFalse(lockdir.exists())

    def test_stale_takeover(self) -> None:
        lockdir = vl._lockdir_for(self.vault, self.root)
        lockdir.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(lockdir)
        # Force the heartbeat 100s old (>> stale=10) → crashed-holder takeover.
        old = time.time() - 100.0
        os.utime(lockdir, (old, old))

        t0 = time.monotonic()
        acquired = False
        with vl.vault_mutex(self.vault, lock_root=self.root, timeout=5.0, stale=10.0):
            acquired = True
        elapsed = time.monotonic() - t0
        self.assertTrue(acquired)
        self.assertLess(elapsed, 2.0, "takeover should be near-immediate, not a timeout wait")

    def test_lock_timeout_on_held_fresh_lock(self) -> None:
        lockdir = vl._lockdir_for(self.vault, self.root)
        lockdir.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(lockdir)
        os.utime(lockdir, None)  # fresh heartbeat → not a takeover candidate
        try:
            t0 = time.monotonic()
            with self.assertRaises(vl.LockTimeout):
                with vl.vault_mutex(self.vault, lock_root=self.root, timeout=0.5, stale=10.0):
                    pass
            elapsed = time.monotonic() - t0
            # It actually waited ~timeout before giving up (didn't fail fast,
            # didn't hang).
            self.assertGreaterEqual(elapsed, 0.4)
            self.assertLess(elapsed, 3.0)
        finally:
            os.rmdir(lockdir)

    def test_atexit_cleanup_removes_held_lockdir(self) -> None:
        # Simulate a process that acquired but never released (crash before
        # __exit__): the atexit-registered cleanup must remove the lockdir.
        lockdir = vl._lockdir_for(self.vault, self.root)
        vl._acquire(lockdir, timeout=5.0, stale=10.0)
        try:
            self.assertTrue(lockdir.exists())
            self.assertIn(str(lockdir), vl._held_lockdirs)
            vl._cleanup_all()  # the function registered with atexit
            self.assertFalse(lockdir.exists())
            self.assertNotIn(str(lockdir), vl._held_lockdirs)
        finally:
            vl._unmark_held(lockdir)
            try:
                os.rmdir(lockdir)
            except OSError:
                pass

    @unittest.skipIf(platform.system() == "Windows", "POSIX signal semantics")
    def test_signal_cleanup_removes_lockdir_in_subprocess(self) -> None:
        # Real proof: a child acquires the lock, we SIGTERM it, and the signal
        # handler must remove the lockdir before the process dies.
        root = Path(tempfile.mkdtemp(prefix="vault-lock-sig-root-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        vault = Path(tempfile.mkdtemp(prefix="vault-lock-sig-vault-"))
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        ready = root / "ready.txt"

        child = textwrap.dedent(
            f"""
            import os, sys, time
            sys.path.insert(0, {str(_HERE)!r})
            import vault_lock as vl
            lockdir = vl._lockdir_for({str(vault)!r}, {str(root)!r})
            vl._acquire(lockdir, timeout=5.0, stale=10.0)
            with open({str(ready)!r}, "w") as f:
                f.write(str(lockdir))
            time.sleep(30)
            """
        )
        proc = subprocess.Popen([sys.executable, "-c", child])
        try:
            # Wait for the child to acquire + announce the lockdir.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not ready.exists():
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "child never acquired the lock")
            lockdir = Path(ready.read_text().strip())
            self.assertTrue(lockdir.exists(), "lockdir missing after child acquired")

            proc.terminate()  # SIGTERM
            proc.wait(timeout=5.0)

            # The signal handler should have removed the lockdir on the way out.
            gone_deadline = time.monotonic() + 2.0
            while time.monotonic() < gone_deadline and lockdir.exists():
                time.sleep(0.02)
            self.assertFalse(lockdir.exists(), "signal handler did not clean up the lockdir")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
