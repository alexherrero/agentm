"""What dreaming has already done, and what it still owes.

The coverage ledger and the pending-work queues live in the daemon's index
database, because the daemon owns that database and no Python module touches it.
This is how the stages ask.

The same seam `storage_rules.py` opened for the filing contract, for the same
reason: one implementation of a decision, asked over a command, rather than a
copy in each half that drifts. A stage carrying its own idea of what "already
processed" means would eventually disagree with the daemon, and the disagreement
would show up as either repeated work or work that silently stopped.

Every call is cheap and none of them spends a model call. Asking whether
something needs doing is always free; only the doing costs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

DAEMON_BIN = os.environ.get("AGENTMD", "agentmd")
_TIMEOUT_SECONDS = 30


class LedgerUnavailable(RuntimeError):
    """No answer from the daemon about what has been done or is owed.

    Raised rather than defaulted. A stage that assumed "nothing has been done"
    would redo the corpus at full price; one that assumed "everything has been
    done" would silently stop working. Neither is a safe default, so callers
    decide — and every caller in `dream.py` decides the same way, by skipping
    the stage and saying so in the digest.
    """


def _run(args: list) -> str:
    binary = shutil.which(DAEMON_BIN) or DAEMON_BIN
    try:
        proc = subprocess.run([binary] + args, capture_output=True, text=True,
                              timeout=_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise LedgerUnavailable(
            f"{DAEMON_BIN} is not on PATH. The daemon owns the coverage ledger "
            f"and the work queues; without it nothing knows what has already "
            f"been done. Set $AGENTMD to a built binary."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LedgerUnavailable(
            f"{DAEMON_BIN} did not answer within {_TIMEOUT_SECONDS}s."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "no reason given"
        raise LedgerUnavailable(detail)
    return proc.stdout


def _json(args: list):
    out = _run(args + ["--json"])
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError as exc:
        raise LedgerUnavailable(
            f"{DAEMON_BIN} {' '.join(args)} returned something that is not JSON"
        ) from exc


# ── the coverage ledger ────────────────────────────────────────────────────

def stages() -> list:
    """What each stage has recorded, one row per stage and version."""
    return _json(["ledger"]) or []


def pending(stage: str) -> dict:
    """What `stage` still owes over its eligible population.

    The reasons are the design's three plus the two it does not enumerate:
    never attempted, fingerprint changed, version stale, plus retry and skipped.
    """
    return _json(["ledger", "--stage", stage, "--pending"]) or {}


# ── the pending-work queues ────────────────────────────────────────────────

def enqueue(owner: str, target: str, reason: str) -> None:
    """Record that `owner` owes work on `target`.

    Discovery is decoupled from repair: a stage that finds a gap another stage
    owns says so and moves on. Idempotent by (owner, target), so the nightly
    reconcile scan re-discovering the same gap does not enqueue it again.
    """
    _run(["queue", "--owner", owner, "--enqueue", target, "--reason", reason])


def queues(owner: str = "") -> list:
    """Every queue's depth, oldest item, cursor and parked items.

    The two numbers the dashboard reads are depth and the age of the oldest
    item, and the threshold is on age: fifty fresh items on a Tuesday is a
    Tuesday, and one item three days old means the drain has stalled.
    """
    args = ["queue"]
    if owner:
        args += ["--owner", owner]
    return _json(args) or []


def parked(owner: str) -> list:
    """The items that failed too many times and stopped being retried.

    Surfaced in the digest, which is the whole point of parking rather than
    dropping: nothing retries silently forever, and nothing stops silently
    either.
    """
    for view in queues(owner):
        if view.get("owner") == owner:
            return view.get("parked") or []
    return []


# ── the corpus's shape ─────────────────────────────────────────────────────

def entity_mentions(min_mentions: int = 1, limit: int = 0) -> list:
    """What the corpus mentions, most-mentioned first, and whether each has a
    file of its own.

    The rollup stage's input. An entity mentioned in forty notes with no entity
    file is the design's own worked example of a gap one stage finds and
    another owns.
    """
    args = ["graph", "--entities", "--min", str(min_mentions)]
    if limit:
        args += ["--limit", str(limit)]
    return _json(args) or []


def dangling_targets(min_sources: int = 1, limit: int = 0) -> list:
    """What the corpus links to and does not have, with who links to it.

    The stub-synthesis stage's input. A dangling link is a fact about the
    corpus — somebody wrote it and meant it — which is why the link index keeps
    unresolved rows rather than dropping them.
    """
    args = ["graph", "--dangling", "--min", str(min_sources)]
    if limit:
        args += ["--limit", str(limit)]
    return _json(args) or []


def backlinks(rel: str) -> list:
    """Every link pointing at `rel`.

    Note the field overload this inherits from the index: on a backlink query
    `resolved` carries the *source* path, because from the target's point of
    view the interesting path is where the link came from.
    """
    return _json(["graph", "--backlinks", rel]) or []


def available() -> bool:
    """Whether the daemon can be reached at all.

    For a stage deciding whether to run rather than one deciding what to do.
    Cheap, and it fails to False rather than raising, because "should I try" is
    a different question from "what is the answer".
    """
    try:
        _json(["ledger"])
        return True
    except LedgerUnavailable:
        return False
