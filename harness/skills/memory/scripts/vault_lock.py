#!/usr/bin/env python3
"""vault_lock — the Phase-0 vault-write protocol (R4 concurrency floor).

The write-safety library that lets N≥2 concurrent agent sessions write the
shared Google-Drive-synced MemoryVault without lost updates or torn files.
This is the floor `harness_memory.py` and the `/memory save`/`evolve` scripts
route their vault writes through (V5-0); the future Phase-1 singleton broker
(V5-9) must also route through *this* module (writer #2), so the protocol has
exactly one implementation.

Public surface:

    atomic_write(path, content, *, fsync=True) -> Path
        The ONE canonical writer. Write bytes to ``<path>.tmp`` in the same
        directory, ``os.fsync`` the temp fd, then ``os.replace`` onto the
        target. Bytes-mode (str is encoded utf-8) so LF-only line endings are
        preserved byte-for-byte across Mac/Linux/Windows (the V4 Windows-CI
        fix). Plain ``fsync``, NOT ``F_FULLFSYNC`` (DC-5): macOS fsync is not
        a durability guarantee, but it keeps each Drive-uploaded snapshot
        internally consistent, which is the property we need — the cloud copy
        is the durability backstop.

    content_hash(data) -> str
        sha256 hex of bytes/str. The compare-and-swap (CAS) currency that
        replaces the V4 mtime check (R4 rule 4: Drive re-downloads make mtime
        weak; content hash does not lie).

    vault_mutex(vault_path, *, timeout=10.0, stale=10.0, lock_root=None)
        One global per-vault advisory mutex as a context manager. The lockdir
        lives on a LOCAL, non-synced path — ``<lock_root or
        ~/.cache/agentm/locks>/<sha256(realpath(vault))>/lock`` — NEVER inside
        the synced vault (R4 rule 1). Acquisition is ``mkdir`` (atomic /
        O_EXCL-equivalent) with bounded block-and-backoff up to ``timeout``,
        then ``LockTimeout``. Liveness is an mtime heartbeat (the lockdir's own
        mtime, touched every ``stale``/2 s by a daemon thread) — NO PIDs in the
        lock (R4 rule 3). A lock whose heartbeat is older than ``stale`` is
        declared crashed and taken over. ``atexit`` + SIGINT/SIGTERM handlers
        remove any still-held lockdirs on process death. ``lock_root`` is
        injectable so tests never pollute the real ``~/.cache``.

Errors:

    LockTimeout                  — vault_mutex could not acquire within timeout.
    ConcurrentModificationError  — CAS mismatch: the file changed between read
                                   and write (re-read, re-apply, retry). Canonical
                                   home for the error `harness_memory` re-exports.

Stdlib-only. POSIX-first (the lock works on Windows too — mkdir is atomic
there — but signal-driven cleanup is best-effort off POSIX). No third-party
deps. See agentm ROADMAP § V5-0, ADR 0012, and [[research-concurrent-vault-writes]]
(R4 — the five hard rules + hazards table).
"""
from __future__ import annotations

import atexit
import hashlib
import os
import signal
import threading
import time
from pathlib import Path

__all__ = [
    "atomic_write",
    "content_hash",
    "vault_mutex",
    "LockTimeout",
    "ConcurrentModificationError",
]


# -----------------------------------------------------------------------------
# Errors (the one vault-write error vocabulary)
# -----------------------------------------------------------------------------

class LockTimeout(RuntimeError):
    """Raised by `vault_mutex` when the per-vault lock could not be acquired
    within `timeout` seconds and the holder's heartbeat is still fresh (not
    stale → not a takeover candidate). The caller should back off and retry,
    or surface the contention; it must NOT write the vault un-serialized."""


class ConcurrentModificationError(RuntimeError):
    """Raised when a content-hash CAS check fails: the file changed between the
    caller's read and its write — another agent or device modified it. The
    caller is expected to re-read, re-apply its change, and retry.

    Canonical home for the error type; `harness_memory.safe_write_replace_style`
    re-exports it so the codebase keeps one error vocabulary (V5-0 DC-3)."""


# -----------------------------------------------------------------------------
# Canonical atomic writer + hash
# -----------------------------------------------------------------------------

def atomic_write(path: Path | str, content: str | bytes, *, fsync: bool = True) -> Path:
    """Atomically write `content` to `path` via temp→fsync→rename (R4 rule 2).

    Bytes-mode: `str` is encoded utf-8 with no newline translation, so LF-only
    endings survive byte-for-byte. The temp file is created in the SAME
    directory as the target (so `os.replace` is a same-filesystem atomic
    rename), its fd is fsync'd (unless `fsync=False`), then renamed onto the
    target. The parent directory is created if absent. Returns the target path.

    `fsync` uses plain `os.fsync`, never `F_FULLFSYNC` (DC-5).
    """
    path = Path(path)
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def content_hash(data: str | bytes) -> str:
    """Return the sha256 hex digest of `data` (str encoded utf-8). The CAS
    currency — stable across processes/devices, unlike mtime (R4 rule 4)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# -----------------------------------------------------------------------------
# Per-vault advisory mutex (local lockdir + mtime heartbeat, no PIDs)
# -----------------------------------------------------------------------------

def _default_lock_root() -> Path:
    """`~/.cache/agentm/locks`, honoring `XDG_CACHE_HOME` when set (so the lock
    base is overridable and CI/dev machines have a second escape valve beyond
    the injectable `lock_root`). NEVER inside the synced vault (R4 rule 1)."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agentm" / "locks"


def _lockdir_for(vault_path: Path | str, lock_root: Path | str | None) -> Path:
    """Resolve the local lockdir for `vault_path`: ``<root>/<sha256(realpath
    vault)>/lock``. `realpath` so different symlink aliases to the same physical
    vault collide on one lock (which is what mutual exclusion requires).
    Pure path computation — does not touch the filesystem."""
    root = Path(lock_root) if lock_root is not None else _default_lock_root()
    key = content_hash(os.path.realpath(str(vault_path)))
    return root / key / "lock"


# Process-global registry of currently-held lockdirs, so atexit / signal
# handlers can remove them on abnormal exit. No PIDs live *in* the lock (R4
# rule 3); this is purely in-process bookkeeping for cleanup.
_held_lockdirs: set[str] = set()
_held_lock = threading.Lock()
_atexit_installed = False
_signals_installed = False


def _mark_held(lockdir: Path) -> None:
    with _held_lock:
        _held_lockdirs.add(str(lockdir))


def _unmark_held(lockdir: Path) -> None:
    with _held_lock:
        _held_lockdirs.discard(str(lockdir))


def _cleanup_all(*_args: object) -> None:
    """Remove every lockdir this process still holds. Registered with `atexit`
    and chained from the SIGINT/SIGTERM handlers."""
    with _held_lock:
        dirs = list(_held_lockdirs)
        _held_lockdirs.clear()
    for d in dirs:
        try:
            os.rmdir(d)
        except OSError:
            pass  # already taken over, or gone — best-effort


def _make_signal_handler(prev: object):
    def _handler(signum: int, frame: object) -> None:
        _cleanup_all()
        if callable(prev):
            # A real previous handler (e.g. SIGINT's default raises
            # KeyboardInterrupt) — chain to it so app semantics survive.
            prev(signum, frame)  # type: ignore[operator]
        elif prev == signal.SIG_DFL:
            # Restore the default action and re-raise so the process still
            # terminates as it would have without our handler.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        # prev is SIG_IGN or None → swallow after cleanup.
    return _handler


def _install_cleanup_once() -> None:
    """Idempotently register `atexit` + (main-thread-only) SIGINT/SIGTERM
    cleanup. Signal handlers can only be installed from the main thread, so a
    first acquisition on a worker thread installs `atexit` only; a later
    main-thread acquisition still installs the signal handlers."""
    global _atexit_installed, _signals_installed
    with _held_lock:
        need_atexit = not _atexit_installed
        need_signals = (
            not _signals_installed
            and threading.current_thread() is threading.main_thread()
        )
        if need_atexit:
            _atexit_installed = True
        if need_signals:
            _signals_installed = True
    if need_atexit:
        atexit.register(_cleanup_all)
    if need_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                prev = signal.getsignal(sig)
                signal.signal(sig, _make_signal_handler(prev))
            except (ValueError, OSError, RuntimeError):
                pass  # unsupported on this platform / not installable


def _lock_age(lockdir: Path) -> float | None:
    """Seconds since the lockdir's heartbeat (its mtime), or None if it's gone."""
    try:
        st = os.stat(lockdir)
    except FileNotFoundError:
        return None
    return max(0.0, time.time() - st.st_mtime)


def _heartbeat(lockdir: Path, stop: threading.Event, stale: float) -> None:
    """Touch the lockdir mtime every `stale`/2 s until `stop` is set. Stops
    early (returns) if the lockdir vanishes — i.e. it was taken over."""
    interval = max(0.001, stale / 2.0)
    while not stop.wait(interval):
        try:
            os.utime(lockdir, None)
        except OSError:
            return


def _acquire(lockdir: Path | str, *, timeout: float, stale: float) -> None:
    """Acquire the lockdir via `mkdir` with bounded block-and-backoff. Takes
    over a stale lock (heartbeat older than `stale`). Raises `LockTimeout` if
    the lock is held-and-fresh past `timeout`."""
    _install_cleanup_once()
    lockdir = Path(lockdir)
    lockdir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    backoff = 0.01
    while True:
        try:
            os.mkdir(lockdir)
        except (FileExistsError, PermissionError) as e:
            # Windows can raise PermissionError (WinError 5, "Access is
            # denied") instead of FileExistsError for the identical race --
            # two threads' os.mkdir calls landing close enough together
            # that the loser sees the directory mid-creation/deletion
            # rather than cleanly already-existing. Found by CI, not
            # reasoned about in advance: an 8-thread contention test
            # (ingest_sweep's own concurrency regression suite) reliably
            # triggered it on the Windows runner; the same race never
            # surfaces on POSIX, where mkdir's FileExistsError is the only
            # outcome. Only treat PermissionError as this same "someone
            # else is acquiring it" signal when the directory now actually
            # exists -- otherwise it's a genuine permission problem (e.g.
            # the lock root itself isn't writable), which must still raise.
            if isinstance(e, PermissionError) and not lockdir.exists():
                raise
            age = _lock_age(lockdir)
            if age is None:
                continue  # released between our mkdir-fail and the stat — retry now
            if age > stale:
                # Crashed holder (heartbeat went silent past `stale`): take over.
                # mkdir's atomicity below is the real guard if two waiters race here.
                try:
                    os.rmdir(lockdir)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"could not acquire vault lock at {lockdir} within {timeout}s "
                    f"(held; heartbeat age={age:.2f}s ≤ stale={stale}s)"
                )
            time.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
            backoff = min(backoff * 2, 0.2)
            continue
        else:
            _mark_held(lockdir)
            try:
                os.utime(lockdir, None)  # initial heartbeat = now
            except OSError:
                pass
            return


def _release(lockdir: Path) -> None:
    _unmark_held(lockdir)
    try:
        os.rmdir(lockdir)
    except OSError:
        pass  # taken over (we were stale) or already gone — best-effort


class vault_mutex:  # noqa: N801 — used as a callable context manager, reads as a verb
    """Context manager for the one global per-vault advisory mutex.

    Usage::

        with vault_mutex(vault_path):
            # all shared-vault writes here are serialized against other
            # holders of the same vault's lock
            atomic_write(target, new_content)

    The lockdir is local (never in the synced vault). A daemon thread heartbeats
    the lockdir mtime every `stale`/2 s while held; `__exit__` stops it and
    removes the lockdir. `timeout`≈`stale`=10s by default (DC-6): short/rare
    writes make brief blocking fine, bounding prevents a wedged holder from
    deadlocking the fleet, and stale-takeover recovers crashed writers.

    `lock_root` overrides the lock base (tests inject a temp dir so the real
    `~/.cache` is never touched). Yields the resolved lockdir Path.
    """

    def __init__(
        self,
        vault_path: Path | str,
        *,
        timeout: float = 10.0,
        stale: float = 10.0,
        lock_root: Path | str | None = None,
    ) -> None:
        self.vault_path = vault_path
        self.timeout = timeout
        self.stale = stale
        self.lockdir = _lockdir_for(vault_path, lock_root)
        self._stop: threading.Event | None = None
        self._hb: threading.Thread | None = None

    def __enter__(self) -> Path:
        _acquire(self.lockdir, timeout=self.timeout, stale=self.stale)
        self._stop = threading.Event()
        self._hb = threading.Thread(
            target=_heartbeat,
            args=(self.lockdir, self._stop, self.stale),
            daemon=True,
        )
        self._hb.start()
        return self.lockdir

    def __exit__(self, *_exc: object) -> bool:
        if self._stop is not None:
            self._stop.set()
        if self._hb is not None:
            self._hb.join(timeout=max(1.0, self.stale))
        _release(self.lockdir)
        return False
