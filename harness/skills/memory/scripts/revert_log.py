#!/usr/bin/env python3
"""revert_log — the dreaming pipeline's undo primitive (AG Wave E, dreaming
plan task 1).

Dreaming's dedup/contradiction-triage/compression/crystallization passes make
content-touching writes to vault entries — merges, supersessions, compaction.
Those writes must be undoable before any autonomous corpus-scale mutation is
safe to run (`wiki/designs/agentm-experience-and-dreaming.md` line 74: "a
revert-log primitive must exist before whole-corpus consolidation is safe to
run"). This module is that primitive — nothing above it (dedup, triage,
`/dream`, the runner job) exists yet; this only builds the undo floor.

Locking discipline: the runner design's locked call is that a dream pass
"acquires the mutex around each atomic stage rather than holding it for the
whole pass, so concurrent sessions are never starved"
(`wiki/designs/agentm-runner.md` line 82). `record_and_apply` therefore takes
one `vault_mutex` acquisition per call — callers journal-and-apply one stage
at a time, never wrap a whole multi-stage pass in a single lock.

Journal placement: per R4 rule 1, "NEVER put locks, SQLite, `.git`, or
journals inside the synced vault" — journals are named explicitly. The undo
journal therefore lives on a LOCAL, non-synced path
(`~/.cache/agentm/dream/revert-log/<run_id>.jsonl` by default,
`XDG_CACHE_HOME`-honoring, mirroring `vault_lock`'s lock-root convention),
never under the vault the dreaming pass mutates.

Byte-fidelity: pre-images are captured and restored as raw bytes
(base64-encoded in the JSON journal) rather than decoded text, so revert is
exact regardless of encoding or line-ending — the same guarantee
`vault_lock.atomic_write` gives a fresh write.

Public surface:

    RevertLog(vault_path, *, log_root=None, lock_root=None,
              timeout=10.0, stale=10.0)
        One journal per vault. `log_root` / `lock_root` are injectable so
        tests never touch the real `~/.cache`.

    RevertLog.record_and_apply(run_id, stage, mutations) -> entry_id
        `mutations` is an iterable of `(path, new_content_or_None)` pairs
        (`new_content=None` means delete). Captures every touched path's
        pre-image, journals it, then applies the mutations — all inside one
        `vault_mutex` acquisition. Returns the journaled entry's id.

    RevertLog.revert(run_id, entry_id=None)
        Restores the pre-images for one journaled stage (`entry_id`) or, if
        omitted, every stage of `run_id` in reverse order — each stage's
        restore is its own `vault_mutex` acquisition, mirroring
        `record_and_apply`'s per-stage discipline.

Errors:

    RevertLogError    — base error for this module.
    UnknownRunError    — `revert()` was asked for a run/entry the journal
                          has no record of.

Stdlib-only. See `wiki/designs/agentm-experience-and-dreaming.md`,
`wiki/designs/agentm-runner.md`, `wiki/designs/agentm-memory-system.md`
("Capture — the write protocol"), and [[research-concurrent-vault-writes]].
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vault_lock import atomic_write, content_hash, vault_mutex  # noqa: E402

__all__ = ["RevertLog", "RevertLogError", "UnknownRunError"]

# `Tuple[Union[...], ...]`, not the `X | Y` PEP-604 spelling — this alias is
# a module-level value evaluated at import time (not a deferred annotation),
# and the repo's floor is Python 3.9 (no runtime `|` union support there).
Mutation = Tuple[Union[Path, str], Optional[Union[str, bytes]]]


class RevertLogError(RuntimeError):
    """Base error for revert-log operations."""


class UnknownRunError(RevertLogError):
    """Raised when `revert()` is asked for a run-id or entry-id the journal
    has no record of."""


def _default_log_root() -> Path:
    """`~/.cache/agentm/dream/revert-log`, honoring `XDG_CACHE_HOME` — the
    same local, non-synced convention `vault_lock._default_lock_root` uses
    for the mutex (R4 rule 1 names journals as coordination state too)."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agentm" / "dream" / "revert-log"


def _to_bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


class RevertLog:
    """Append-only per-run undo journal for dreaming's content-touching
    writes. One journal file per run (`<log_root>/<run_id>.jsonl`), one JSON
    line per atomic-stage call to `record_and_apply`."""

    def __init__(
        self,
        vault_path: Path | str,
        *,
        log_root: Path | str | None = None,
        lock_root: Path | str | None = None,
        timeout: float = 10.0,
        stale: float = 10.0,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.log_root = Path(log_root) if log_root is not None else _default_log_root()
        self.lock_root = lock_root
        self.timeout = timeout
        self.stale = stale

    def _journal_path(self, run_id: str) -> Path:
        return self.log_root / f"{run_id}.jsonl"

    def _mutex(self):
        return vault_mutex(
            self.vault_path, timeout=self.timeout, stale=self.stale, lock_root=self.lock_root
        )

    def record_and_apply(
        self, run_id: str, stage: str, mutations: Iterable[Mutation]
    ) -> str:
        """Journal the pre-image of every path in `mutations`, then apply
        the mutations — both inside ONE `vault_mutex` acquisition scoped to
        this stage only. `mutations` entries are `(path, new_content)`;
        `new_content=None` deletes the path (dreaming never hard-deletes
        vault entries today, but the primitive supports it for
        completeness — e.g. a mutation that removes a stray temp artifact
        it itself created earlier in the same stage). Returns the journaled
        entry's id so the caller can revert just this stage later."""
        entry_id = uuid.uuid4().hex
        mutations = [(Path(p), c) for p, c in mutations]

        with self._mutex():
            pre_images = []
            for path, _new_content in mutations:
                existed = path.exists()
                data = path.read_bytes() if existed else b""
                pre_images.append(
                    {
                        "path": str(path),
                        "existed": existed,
                        "content_b64": base64.b64encode(data).decode("ascii"),
                        "hash": content_hash(data) if existed else None,
                    }
                )

            record = {
                "entry_id": entry_id,
                "run_id": run_id,
                "stage": stage,
                "ts": time.time(),
                "pre_images": pre_images,
            }
            self.log_root.mkdir(parents=True, exist_ok=True)
            journal = self._journal_path(run_id)
            with open(journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())

            for path, new_content in mutations:
                if new_content is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, _to_bytes(new_content))

        return entry_id

    def _read_entries(self, run_id: str) -> list[dict]:
        journal = self._journal_path(run_id)
        if not journal.exists():
            raise UnknownRunError(
                f"no revert-log journal for run {run_id!r} at {journal}"
            )
        entries = []
        with open(journal, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def revert(self, run_id: str, entry_id: str | None = None) -> None:
        """Restore the pre-images journaled for one stage (`entry_id`) or,
        if omitted, every stage of `run_id` in reverse (most-recent-first)
        order — undoing a whole run one stage at a time. Each stage's
        restore is its own `vault_mutex` acquisition, mirroring
        `record_and_apply`'s per-stage-not-per-pass discipline."""
        entries = self._read_entries(run_id)
        if entry_id is not None:
            entries = [e for e in entries if e["entry_id"] == entry_id]
            if not entries:
                raise UnknownRunError(f"no entry {entry_id!r} in run {run_id!r}")
        else:
            entries = list(reversed(entries))

        for entry in entries:
            with self._mutex():
                for pre in entry["pre_images"]:
                    path = Path(pre["path"])
                    if pre["existed"]:
                        data = base64.b64decode(pre["content_b64"])
                        atomic_write(path, data)
                    elif path.exists():
                        path.unlink()
