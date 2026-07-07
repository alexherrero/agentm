#!/usr/bin/env python3
"""dream_confirm.py — the `_dream-staging/` inbox contract (AG Wave E
dreaming plan task 3): staged proposal -> operator confirms -> applied
through the revert-log, never a direct write; an unconfirmed proposal
expires/stays inert rather than silently applying.

This formalizes the procedural contract `dream.py` (task 2) already
exercises: `run_dream()` stages every source-touching disposition to
`_dream-staging/<run_id>/` (a human-readable `digest.md` + per-proposal
`.proposal.md` files + a machine-readable `proposals.json` manifest) but
never applies any of them. This module is the other half — the confirm
flow that reads a staged run's manifest and, ONLY on explicit operator
confirmation, applies one proposal's mutations through task 1's
`revert_log.RevertLog.record_and_apply` (never a direct `atomic_write`),
and the expiry check that keeps an unconfirmed proposal from silently
applying once it goes stale.

Per-run confirmation state lives alongside the staged proposals themselves
(`_dream-staging/<run_id>/state.json`) — not in the synced revert-log
journal (that only ever records dispositions that were actually applied)
and not as a separate coordination store; the staging directory already IS
the inbox this state belongs to.

Public surface:

    list_pending(vault_path, run_id) -> list[PendingProposal]
        Every proposal in the run's manifest, annotated with its current
        confirm/expire state.

    confirm(vault_path, run_id, index, revert_log, *, ttl_days=30, now=None, lock_root=None) -> str
        Applies proposal `index` (1-based, matching the digest's numbering)
        through `revert_log.record_and_apply` and marks it confirmed.
        Raises `ExpiredProposalError` if the proposal is past its TTL —
        NEVER applies an expired proposal, silently or otherwise.
        Raises `AlreadyConfirmedError` on a second confirm of the same
        index. Returns the revert-log entry id. The whole call is
        serialized (per run_id) via `_confirm_lock`, a mutex SEPARATE from
        `revert_log`'s own — see `_confirm_lock`'s docstring for why a
        concurrent confirm() race on state.json is a real, previously-
        unguarded defect this closes.

    expire_stale(vault_path, run_id, *, ttl_days=30, now=None, lock_root=None) -> list[int]
        Marks every still-pending proposal past its TTL as expired (a
        pure state transition — expired proposals are never applied by
        this call or any other path) and returns their indices. Serialized
        the same way as `confirm()`.

Errors:

    DreamConfirmError       — base error for this module.
    UnknownRunError          — no manifest for `run_id`.
    UnknownProposalError     — `index` is not in the run's manifest.
    ExpiredProposalError     — `confirm()` on a past-TTL proposal.
    AlreadyConfirmedError    — `confirm()` on an already-confirmed proposal.

Stdlib-only. See `dream.py`, `revert_log.py`,
`wiki/designs/agentm-experience-and-dreaming.md`, and
`wiki/designs/agentm-runner.md` ("the runner *proposes* rather than
applies... the digest carries it for the operator to confirm").
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from revert_log import RevertLog  # noqa: E402
from vault_lock import atomic_write, vault_mutex  # noqa: E402

__all__ = [
    "PendingProposal",
    "list_pending",
    "confirm",
    "expire_stale",
    "DreamConfirmError",
    "UnknownRunError",
    "UnknownProposalError",
    "ExpiredProposalError",
    "AlreadyConfirmedError",
]

DEFAULT_TTL_DAYS = 30.0


class DreamConfirmError(RuntimeError):
    """Base error for the dream-staging confirm flow."""


class UnknownRunError(DreamConfirmError):
    """No `_dream-staging/<run_id>/proposals.json` manifest exists."""


class UnknownProposalError(DreamConfirmError):
    """`index` is not present in the run's manifest."""


class ExpiredProposalError(DreamConfirmError):
    """`confirm()` was called on a proposal past its TTL. The proposal is
    NOT applied — an expired proposal never applies, silently or not."""


class AlreadyConfirmedError(DreamConfirmError):
    """`confirm()` was called on a proposal already confirmed in a prior
    call — confirmation is one-shot, not idempotent-reapply."""


@dataclass
class PendingProposal:
    index: int
    stage: str
    kind: str
    paths: list
    summary: str
    mutations: list
    status: str  # "pending" | "confirmed" | "expired"


def _staging_dir(vault_path: Path, run_id: str) -> Path:
    return Path(vault_path) / "_dream-staging" / run_id


def _manifest_path(vault_path: Path, run_id: str) -> Path:
    return _staging_dir(vault_path, run_id) / "proposals.json"


def _state_path(vault_path: Path, run_id: str) -> Path:
    return _staging_dir(vault_path, run_id) / "state.json"


def _load_manifest(vault_path: Path, run_id: str) -> dict:
    path = _manifest_path(vault_path, run_id)
    if not path.exists():
        raise UnknownRunError(f"no dream-staging manifest for run {run_id!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(vault_path: Path, run_id: str) -> dict:
    path = _state_path(vault_path, run_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(vault_path: Path, run_id: str, state: dict) -> None:
    atomic_write(_state_path(vault_path, run_id), json.dumps(state, indent=2))


def _confirm_lock(vault_path: Path, run_id: str, *, lock_root: Path | str | None):
    """The mutex guarding `state.json`'s whole read-modify-write for one
    run — a SEPARATE lock from `revert_log.RevertLog`'s own `vault_mutex`
    (keyed by a synthetic path derived from `vault_path` + `run_id`, not the
    bare `vault_path`), so `confirm()` can hold this lock across its own
    entire body, including the nested `record_and_apply` call, without
    deadlocking on `vault_mutex`'s non-reentrant `mkdir`-based lock.

    Without this, `confirm()`'s `_load_state` / `record_and_apply` /
    `_save_state` sequence races under concurrent callers: two confirms on
    different (or the same) proposal index can interleave their state
    read-modify-write, silently losing a `confirmed` flag even though the
    underlying mutation already applied — which defeats the
    `AlreadyConfirmedError` one-shot guard and can cause a second
    `record_and_apply` call to journal already-mutated content as its
    "pre-image", orphaning the true original from the revert log."""
    lock_key = f"{Path(vault_path)}::dream-confirm::{run_id}"
    return vault_mutex(lock_key, lock_root=lock_root)


def _proposal_status(entry: dict, index: int, state: dict, *, ttl_days: float, now: float) -> str:
    per_index = state.get(str(index), {})
    if per_index.get("confirmed"):
        return "confirmed"
    if per_index.get("expired"):
        return "expired"
    staged_at = state.get("_manifest_staged_at", 0.0)
    if now - staged_at > ttl_days * 86400:
        return "expired"
    return "pending"


def list_pending(vault_path: Path, run_id: str, *, ttl_days: float = DEFAULT_TTL_DAYS, now: float | None = None) -> list:
    """Every proposal in `run_id`'s manifest, annotated with its current
    status. `now` is injectable for tests (defaults to `time.time()`)."""
    now = now if now is not None else time.time()
    manifest = _load_manifest(vault_path, run_id)
    state = _load_state(vault_path, run_id)
    state.setdefault("_manifest_staged_at", manifest["staged_at"])

    pending = []
    for entry in manifest["proposals"]:
        status = _proposal_status(entry, entry["index"], state, ttl_days=ttl_days, now=now)
        pending.append(
            PendingProposal(
                index=entry["index"],
                stage=entry["stage"],
                kind=entry["kind"],
                paths=entry["paths"],
                summary=entry["summary"],
                mutations=entry["mutations"],
                status=status,
            )
        )
    return pending


def expire_stale(
    vault_path: Path,
    run_id: str,
    *,
    ttl_days: float = DEFAULT_TTL_DAYS,
    now: float | None = None,
    lock_root: Path | str | None = None,
) -> list:
    """Mark every still-pending proposal in `run_id` past its TTL as
    expired. Returns the indices marked. Pure state transition — an
    expired proposal is never applied by this call or any later `confirm()`
    (that raises `ExpiredProposalError` instead). The whole read-modify-
    write of `state.json` is serialized against concurrent `confirm()` /
    `expire_stale()` calls on the same run via `_confirm_lock` (see its
    docstring for why this can't just reuse `revert_log`'s own mutex)."""
    now = now if now is not None else time.time()
    with _confirm_lock(vault_path, run_id, lock_root=lock_root):
        manifest = _load_manifest(vault_path, run_id)
        state = _load_state(vault_path, run_id)
        state.setdefault("_manifest_staged_at", manifest["staged_at"])

        expired = []
        for entry in manifest["proposals"]:
            index = entry["index"]
            status = _proposal_status(entry, index, state, ttl_days=ttl_days, now=now)
            if status == "expired" and not state.get(str(index), {}).get("expired"):
                state.setdefault(str(index), {})["expired"] = True
                expired.append(index)

        if expired:
            _save_state(vault_path, run_id, state)
        return expired


def confirm(
    vault_path: Path,
    run_id: str,
    index: int,
    revert_log: RevertLog,
    *,
    ttl_days: float = DEFAULT_TTL_DAYS,
    now: float | None = None,
    lock_root: Path | str | None = None,
) -> str:
    """Apply proposal `index` (1-based) of `run_id` through
    `revert_log.record_and_apply` — the ONLY path a staged proposal ever
    applies through; this function never calls `atomic_write` on a source
    entry itself. Raises `ExpiredProposalError` (never applies) if the
    proposal is past its TTL, `AlreadyConfirmedError` if it was already
    confirmed, `UnknownProposalError` if `index` isn't in the manifest.

    The ENTIRE body — state load, the status check, `record_and_apply`,
    and the state save — runs under `_confirm_lock`, so two concurrent
    `confirm()` calls on the same run (even on different proposal indices)
    can never interleave their `state.json` read-modify-write. Without this,
    a race can silently lose a `confirmed` flag after its mutation already
    applied, defeating the `AlreadyConfirmedError` one-shot guard and
    letting a second `record_and_apply` journal already-mutated content as
    its "pre-image" — orphaning the true original from the revert log."""
    now = now if now is not None else time.time()
    with _confirm_lock(vault_path, run_id, lock_root=lock_root):
        manifest = _load_manifest(vault_path, run_id)
        state = _load_state(vault_path, run_id)
        state.setdefault("_manifest_staged_at", manifest["staged_at"])

        entry = next((e for e in manifest["proposals"] if e["index"] == index), None)
        if entry is None:
            raise UnknownProposalError(f"no proposal {index} in run {run_id!r}")

        status = _proposal_status(entry, index, state, ttl_days=ttl_days, now=now)
        if status == "expired":
            state.setdefault(str(index), {})["expired"] = True
            _save_state(vault_path, run_id, state)
            raise ExpiredProposalError(
                f"proposal {index} of run {run_id!r} expired (staged {now - state['_manifest_staged_at']:.0f}s "
                f"ago, ttl {ttl_days} day(s)) — never applied"
            )
        if status == "confirmed":
            raise AlreadyConfirmedError(f"proposal {index} of run {run_id!r} was already confirmed")

        mutations = [(Path(path), content) for path, content in entry["mutations"]]
        entry_id = revert_log.record_and_apply(run_id, entry["stage"], mutations)

        state.setdefault(str(index), {})["confirmed"] = True
        state[str(index)]["entry_id"] = entry_id
        _save_state(vault_path, run_id, state)
        return entry_id
