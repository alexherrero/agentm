"""goal_contract.py — the goal-contract's anti-gaming invariants as a runtime
guard (PLAN-wave-e-scheduled-surfaces task 3; agentm-goal-contract.md).

The design names two invariants, locked by operator ruling, but notes they
were "a design contract the runner build must honor, not yet a runtime
guard" until this lands: (1) the done-check is uneditable by the running
agent, and (2) completion is confirmed only after green deterministic gates
AND a cold /review — never self-certified. This module is that guard.

Scope: this is the Decide step (step 7 of 7 in the goal loop) plus the
done-check integrity check it depends on — not the full seven-step loop.
The other six steps (limit check, safety pre-check, plan/act, verify,
persist) already exist, reused from `/work`'s own machinery per the design's
"vs /work" section; this module is the one piece that was missing.

Uneditable done-check: a running agent has ordinary filesystem write access,
so "uneditable" can't mean OS-level file locking — it means the same thing
AGENTS.md rule 5 already means for a test the agent might be tempted to
weaken: an edit is detected and the claim is refused, not silently
prevented. A content-hash fingerprint taken at goal start, re-checked at
decide time, is that detection.

Never self-certified: `decide()` cannot return "done" from `gates_green`
alone. A separate, explicit `cold_review_confirmed` signal is required —
the caller must have actually gotten that confirmation from a fresh-context
review dispatch (a cold /review sub-agent); this module only enforces that
the gate can't be bypassed, it doesn't perform the dispatch itself (that is
host-loop orchestration, out of this module's scope).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Exit = Literal["done", "continue", "needs-operator-decision"]


def snapshot_done_check(done_check_path: str | Path) -> str:
    """A content-hash fingerprint of the done-check file, taken at goal start."""
    data = Path(done_check_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def done_check_tampered(done_check_path: str | Path, snapshot: str) -> bool:
    """True iff the done-check's content no longer matches the start-of-goal
    snapshot — the running agent (or anything else) edited it since."""
    return snapshot_done_check(done_check_path) != snapshot


@dataclass(frozen=True)
class Decision:
    exit: Exit
    reason: str


def decide(
    *,
    gates_green: bool,
    done_check_path: str | Path,
    done_check_snapshot: str,
    cold_review_confirmed: bool,
) -> Decision:
    """The Decide step (loop step 7): done only under both anti-gaming
    invariants, checked in order — tamper detection first (an agent that
    edited its own done-check must never reach the self-certification
    question at all, tampered-but-unconfirmed and tampered-but-confirmed
    are the same refusal), then the no-self-certification gate.
    """
    if done_check_tampered(done_check_path, done_check_snapshot):
        return Decision(
            "needs-operator-decision",
            "done-check content changed since the goal started — the running "
            "agent cannot edit its own done-check; an operator must confirm "
            "the change was legitimate before this goal can proceed.",
        )
    if not gates_green:
        return Decision("continue", "deterministic gates are not green yet")
    if not cold_review_confirmed:
        return Decision(
            "continue",
            "gates are green, but completion is never self-certified — "
            "awaiting a cold /review confirmation",
        )
    return Decision("done", "deterministic gates green + cold /review confirmed")
