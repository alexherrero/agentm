#!/usr/bin/env python3
"""The breaker that pauses enrichment's auto-apply when a meter crosses its line.

`dream_confirm.check_stage_anomaly` already decides whether *this cycle* is
anomalous, against a rolling trailing history, refusing to judge on a cold start
and never recording an anomalous count into the baseline it is judged against.
That is the detector and it is not changed here.

What it does not do is *stay* tripped. It recomputes every cycle, so a corpus
that spiked on Tuesday and looked ordinary on Wednesday resumes on Wednesday with
nobody having looked at Tuesday. This module is the latch on top: once a meter
crosses its line, enrichment's auto-apply stays paused until a person clears it.

# Why a latch rather than a threshold check each night

The failure this guards is a pass that has started writing something wrong. If
the wrongness is steady rather than spiking, tonight's numbers look like last
night's and a per-cycle check sees nothing to report — the corpus converges at a
constant rate and the breaker never fires twice.

A latch also makes the resume a decision somebody made. "It stopped alarming" and
"somebody looked" are different states, and only one of them is a reason to start
writing to the corpus again.

# What acknowledging means

Clearing the breaker records the reading that was acknowledged. The same reading
does not trip again — the operator has seen it and said to continue. A *worse*
reading does, because that is new information rather than the thing they already
judged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import engine_state  # noqa: E402
from typing import Optional

# The pause records are per-stage machine state; they live in the engine
# state directory (filing-v2 part 2a), joined at the one call site below.


@dataclass
class Trip:
    """One reading that crossed a line, and what it was."""

    meter: str
    value: float
    threshold: float
    # `direction` is which way is bad, carried so the record reads correctly a
    # month later without the reader having to remember which meters fall.
    direction: str = "above"

    def reading(self) -> str:
        """The identity of this reading, for deciding whether an acknowledgement
        covers it.

        Rounded, because a meter that moves in the sixteenth decimal is the same
        reading as far as a person acknowledging it is concerned, and treating it
        as new would re-alarm every night on noise.
        """
        return f"{self.meter}:{self.value:.4f}:{self.direction}"

    def crossed(self) -> bool:
        return self.value > self.threshold if self.direction == "above" \
            else self.value < self.threshold


@dataclass
class BreakerState:
    """Whether auto-apply may run, and why not."""

    open: bool
    stage: str
    reason: str = ""
    tripped_at: str = ""
    reading: str = ""
    acknowledged_reading: str = ""
    acknowledged_at: str = ""
    acknowledged_by: str = ""

    def may_auto_apply(self) -> bool:
        """The one question every caller asks.

        Phrased as a permission rather than a status so a caller reads
        `if state.may_auto_apply()` and cannot get the polarity backwards, which
        is the mistake that would quietly resume a paused pass.
        """
        return not self.open


def _path(vault_path: Path, stage: str) -> Path:
    del vault_path  # engine state left the vault (filing-v2 part 2a)
    return engine_state.engine_state_dir() / f"{stage}-breaker.json"


def _read(vault_path: Path, stage: str) -> dict:
    try:
        data = json.loads(_path(vault_path, stage).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(vault_path: Path, stage: str, data: dict) -> None:
    p = _path(vault_path, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")


def state(vault_path: Path, stage: str) -> BreakerState:
    """What the breaker says right now.

    A missing or unreadable file means closed. Failing open is deliberate: this
    guards a *convenience*, and a breaker that jammed shut because a JSON file
    got truncated would stop the nightly pass over an unrelated fault. The cost
    of failing open is one more cycle of auto-apply before somebody notices; the
    cost of failing shut is a memory that quietly stops maintaining itself.
    """
    data = _read(vault_path, stage)
    return BreakerState(
        open=bool(data.get("open")),
        stage=stage,
        reason=str(data.get("reason") or ""),
        tripped_at=str(data.get("tripped_at") or ""),
        reading=str(data.get("reading") or ""),
        acknowledged_reading=str(data.get("acknowledged_reading") or ""),
        acknowledged_at=str(data.get("acknowledged_at") or ""),
        acknowledged_by=str(data.get("acknowledged_by") or ""),
    )


def consider(vault_path: Path, stage: str, trip: Trip, *,
             now: Optional[datetime] = None) -> BreakerState:
    """Offer one meter reading to the breaker.

    Trips when the reading crossed its line and is not the reading somebody has
    already acknowledged. Leaves an already-open breaker exactly as it is — the
    first reason is the one worth keeping, and overwriting it with each night's
    fresh numbers would lose what a person is being asked to look at.
    """
    current = state(vault_path, stage)
    if current.open:
        return current
    if not trip.crossed():
        return current
    if trip.reading() == current.acknowledged_reading:
        # Already seen and waved through. A worse reading is a different
        # reading and will not match.
        return current

    now = now or datetime.now(timezone.utc)
    data = _read(vault_path, stage)
    data.update({
        "open": True,
        "stage": stage,
        "reason": (f"{trip.meter} is {trip.value:.4f}, {trip.direction} its line "
                   f"of {trip.threshold:.4f}"),
        "tripped_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reading": trip.reading(),
        "trip": asdict(trip),
    })
    _write(vault_path, stage, data)
    return state(vault_path, stage)


def resume(vault_path: Path, stage: str, *, by: str,
           now: Optional[datetime] = None) -> BreakerState:
    """Clear the breaker, recording who looked and what they were looking at.

    `by` is required and must say something. A resume with no name is a resume
    nobody can be asked about later, and this exists precisely so that starting
    again is a decision somebody made rather than a timeout.
    """
    if not by or not by.strip():
        raise ValueError(
            "resuming needs a name — the breaker exists so that starting again "
            "is a decision somebody made, and an anonymous resume is a timeout "
            "wearing a person's clothes")

    current = state(vault_path, stage)
    if not current.open:
        return current

    now = now or datetime.now(timezone.utc)
    data = _read(vault_path, stage)
    data.update({
        "open": False,
        # The reading that was acknowledged, so the same one does not re-trip
        # tomorrow while a worse one still can.
        "acknowledged_reading": current.reading,
        "acknowledged_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "acknowledged_by": by.strip(),
    })
    _write(vault_path, stage, data)
    return state(vault_path, stage)


def digest_line(st: BreakerState) -> str:
    """One line for the nightly digest.

    Always returns something. A breaker that only appeared in the digest when it
    was open would leave the reader unable to tell "not paused" from "nobody
    checked", which is the same absence-versus-zero confusion the scorecards are
    built to avoid.
    """
    if st.open:
        return (f"⚠ {st.stage}: auto-apply is paused — {st.reason} "
                f"(since {st.tripped_at}). It resumes when someone clears it: "
                f"`agentm-breaker --resume {st.stage} --by <name>`")
    if st.acknowledged_at:
        return (f"{st.stage}: auto-apply is running. Last paused reading was "
                f"cleared by {st.acknowledged_by} at {st.acknowledged_at}")
    return f"{st.stage}: auto-apply is running; the breaker has never tripped"
