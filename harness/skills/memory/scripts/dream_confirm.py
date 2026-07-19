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
import shutil
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
    "AUTO_APPLY_STAGES",
    "DEFAULT_AUTO_APPLY_BATCH_CAP",
    "AutoAppliedBatch",
    "auto_apply_batch",
    "render_auto_applied_json",
    "DEFAULT_REVERT_TTL_DAYS",
    "cleanup_applied_batches",
]

DEFAULT_TTL_DAYS = 30.0

# L1/F5 (ledger finding: applied batches were never cleaned up -- a run's
# _dream-staging/<run_id>/ directory persisted indefinitely, staging TTL
# only ever marks proposals expired, never deletes anything). This is a
# SEPARATE, untuned first-cut window: how long a fully-resolved batch's
# staging directory sticks around after its last live application, before
# `cleanup_applied_batches` removes it. RevertLog's own journal
# (~/.cache/agentm/dream/revert-log/<run_id>.jsonl) is independent of this
# directory and is never touched by cleanup -- undo stays possible after
# the staging dir is gone; only the review/audit copy goes.
DEFAULT_REVERT_TTL_DAYS = 14.0

# The dreaming pipeline's "expire" action (operator ruling, 2026-07-11 --
# see wiki/designs/agentm-experience-and-dreaming.md's amendment log): a
# batch of proposals whose stage is in this set applies automatically, no
# per-batch operator confirm required. Only `compression` qualifies --
# supersession-chain compaction acts on notes that ALREADY carry an
# author/prior-cycle-declared `supersedes:` link (never a freshly-inferred
# relationship), it never deletes a source (only marks the non-head chain
# members `compacted_into`, retiring them from independent-note status),
# and every mutation still runs through `revert_log.record_and_apply` --
# so it is exactly "retire it, reversible via the undo/revert log," the
# ruling's own words for the one action allowed to skip the gate.
#
# `dedup` (merge -- a freshly-inferred similarity match, `kind="merge"`)
# and `contradiction_triage` (keep_both -- flags a relationship for the
# operator to resolve, `kind="keep_both"`) are the ruling's "promote" and
# "link" -- they are DELIBERATELY NOT in this set and must keep requiring
# an explicit `confirm()` call. Do not add either to `AUTO_APPLY_STAGES`
# without a fresh, separate operator ruling -- this is a narrow, specific
# flip, not a general autonomy expansion.
#
# `tidying` (auto-organization part 1, task 3 -- wiki/designs/agentm-auto-
# organization.md, PLAN-auto-org-shelf-and-archive.md's own "Every move
# auto-applies" constraint) joins the set on the same "retire it, reversible
# via the undo/revert log" basis: a tidying move never deletes -- both the
# old and new path are captured in one record_and_apply pre-image pair, so
# it reverts exactly like a compression-stage compaction does -- and it
# only ever acts on an entry lifecycle.py itself has already classified as
# non-exempt and cold (the same anchor chain compute_decay_score uses, not
# a second, independently-invented staleness signal).
AUTO_APPLY_STAGES = frozenset({"compression", "tidying"})

# Standing batch bound for one auto-apply cycle (2026-07-11 cadence
# review). The proving-window's temporary <=100 cap was sized for a short
# calibration period on a then-tiny corpus; the standing number is set
# against the vault's real measured shape instead: the dreaming corpus
# `dream.py` actually scans (the whole vault minus `_inbox`/`_meta`/
# `_harness`/etc.) held 366 entries with only 2 carrying a `supersedes:`
# link at review time -- nowhere near the >=3-chain floor `compression`
# requires, so today's real auto-apply volume is ~0 per cycle. 25 leaves
# generous headroom to clear any realistic weekly backlog while capping
# worst-case blast radius at well under 10% of the current corpus (100
# would have been over a quarter of it) -- see the amendment log for the
# full rationale and the inbox growth data behind it.
DEFAULT_AUTO_APPLY_BATCH_CAP = 25


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


@dataclass
class AutoAppliedBatch:
    """One cycle's confirm-free "expire" apply -- the record `auto_apply_batch`
    returns and `render_auto_applied_json` serializes. `items` is empty (not
    omitted) when nothing qualified this run, so a reader always finds a
    current record rather than a stale one from a prior cycle."""

    run_id: str
    applied_at: float
    stages: frozenset
    batch_cap: int
    items: list  # each: {"index", "stage", "kind", "paths", "summary", "entry_id"}


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
        # L1/F5: the only per-index timestamp `cleanup_applied_batches`
        # needs -- when this proposal was actually applied, so the
        # revert-TTL clock (how long the staging dir sticks around after
        # the last live application) has a real anchor.
        state[str(index)]["confirmed_at"] = now
        _save_state(vault_path, run_id, state)
        return entry_id


def auto_apply_batch(
    vault_path: Path,
    run_id: str,
    revert_log: RevertLog,
    *,
    batch_cap: int = DEFAULT_AUTO_APPLY_BATCH_CAP,
    stages: frozenset = AUTO_APPLY_STAGES,
    now: float | None = None,
    lock_root: Path | str | None = None,
) -> AutoAppliedBatch:
    """Automatically confirm+apply every still-pending proposal in `run_id`
    whose stage is in `stages` (default `AUTO_APPLY_STAGES` -- today just
    `{"compression"}`, the dreaming pipeline's "expire" action per the
    2026-07-11 operator ruling). Applies through the exact same `confirm()`
    path a human calls by hand -- same TTL check, same one-shot guard, same
    `revert_log.record_and_apply` -- so an auto-applied item is provably
    indistinguishable, in the revert log, from a manually-confirmed one.

    Applies at most `batch_cap` qualifying proposals per call, lowest-index
    (oldest) first; any remainder stays `pending` for a later cycle or a
    manual `confirm()` -- this is what keeps one run from ever touching
    more than `batch_cap` notes at once.

    `promote` (dedup/merge) and `link` (contradiction-triage/keep_both) are
    NOT in `stages` by default and this function must never be called with
    them added without a fresh, separate operator ruling -- see
    `AUTO_APPLY_STAGES`'s own docstring.

    Returns an `AutoAppliedBatch` describing exactly what applied (empty
    `items` if nothing qualified) -- always a real record, never `None`, so
    the digest and the auto-expired-batch log always have something
    current to report, even on a zero-item cycle."""
    now = now if now is not None else time.time()
    pending = [
        p for p in list_pending(vault_path, run_id, now=now)
        if p.status == "pending" and p.stage in stages
    ]
    pending.sort(key=lambda p: p.index)

    items: list = []
    for p in pending[:batch_cap]:
        entry_id = confirm(vault_path, run_id, p.index, revert_log, now=now, lock_root=lock_root)
        items.append({
            "index": p.index,
            "stage": p.stage,
            "kind": p.kind,
            "paths": p.paths,
            "summary": p.summary,
            "entry_id": entry_id,
        })

    return AutoAppliedBatch(
        run_id=run_id,
        applied_at=now,
        stages=frozenset(stages),
        batch_cap=batch_cap,
        items=items,
    )


def render_auto_applied_json(batch: AutoAppliedBatch) -> str:
    """The machine-readable record of one auto-apply cycle -- written to
    both `_dream-staging/<run_id>/auto-expired.json` (the per-run detail,
    sibling to `digest.md`/`proposals.json`/`state.json`) and
    `_meta/dream-auto-expired-latest.json` (the stable, run-id-free
    pointer a later reader -- e.g. a console/dashboard surface -- reads
    without enumerating `_dream-staging/*/`). Same content both places;
    the "latest" file is fully overwritten every cycle, including a
    zero-item one, so it never goes stale."""
    return json.dumps(
        {
            "run_id": batch.run_id,
            "applied_at": batch.applied_at,
            "stages": sorted(batch.stages),
            "batch_cap": batch.batch_cap,
            "count": len(batch.items),
            "items": batch.items,
            "revert": {
                "how": (
                    "Python API (revert_log.py has no CLI): "
                    "from revert_log import RevertLog; "
                    "RevertLog(vault_path).revert(" + json.dumps(batch.run_id) + ", entry_id) "
                    "reverts one item (use its own entry_id from `items[]` below); "
                    "RevertLog(vault_path).revert(" + json.dumps(batch.run_id) + ") "
                    "with no entry_id reverts every stage of this run, in reverse order."
                ),
                "run_id": batch.run_id,
            },
        },
        indent=2,
        sort_keys=True,
    )


def cleanup_applied_batches(
    vault_path: Path,
    *,
    ttl_days: float = DEFAULT_TTL_DAYS,
    revert_ttl_days: float = DEFAULT_REVERT_TTL_DAYS,
    now: float | None = None,
) -> list:
    """Delete `_dream-staging/<run_id>/` for every batch that is BOTH fully
    resolved (no proposal still `pending` -- everything is `confirmed` or
    `expired`) AND past its revert grace period. Returns the list of
    `run_id`s removed.

    L1/F5: staging directories persisted indefinitely before this -- the
    TTL only ever marked proposals expired, nothing ever deleted anything.
    Safe by construction: `RevertLog`'s own journal
    (~/.cache/agentm/dream/revert-log/) is a completely separate, local,
    byte-fidelity record of every applied mutation -- it is never read from
    or written to by this function, so undo capability is unaffected by
    what this cleans up.

    The grace-period anchor is the latest `confirmed_at` timestamp across
    the batch's proposals (when the last live application happened); a
    batch where nothing was ever confirmed (fully auto-expired, nothing
    qualified for auto-apply) anchors on `staged_at + ttl_days` instead --
    the moment the last proposal in it could have gone stale.

    Best-effort per batch: a manifest/state read failure or an OS error
    during removal skips that run_id rather than raising, so one malformed
    or concurrently-touched staging dir never blocks cleanup of the rest.
    """
    now = now if now is not None else time.time()
    staging_root = Path(vault_path) / "_dream-staging"
    if not staging_root.is_dir():
        return []

    removed = []
    for entry in sorted(staging_root.iterdir()):
        if not entry.is_dir():
            continue
        run_id = entry.name
        try:
            manifest = _load_manifest(vault_path, run_id)
            state = _load_state(vault_path, run_id)
        except (DreamConfirmError, OSError, json.JSONDecodeError):
            continue

        state.setdefault("_manifest_staged_at", manifest["staged_at"])
        pending_exists = False
        confirmed_ats = []
        for e in manifest["proposals"]:
            index = e["index"]
            status = _proposal_status(e, index, state, ttl_days=ttl_days, now=now)
            if status == "pending":
                pending_exists = True
                break
            per_index = state.get(str(index), {})
            if per_index.get("confirmed") and "confirmed_at" in per_index:
                confirmed_ats.append(per_index["confirmed_at"])
        if pending_exists:
            continue

        anchor = max(confirmed_ats) if confirmed_ats else state["_manifest_staged_at"] + ttl_days * 86400
        if now - anchor <= revert_ttl_days * 86400:
            continue

        try:
            shutil.rmtree(entry)
        except OSError:
            continue
        removed.append(run_id)

    return removed
