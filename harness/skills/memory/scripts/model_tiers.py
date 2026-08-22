"""Which model tier a dreaming job may run on.

Two seams in this package have returned `False` since they were written:
`dream.cheap_model_tier_available()` and
`dream_confirm.higher_tier_model_available()`. Each carries a comment saying it
is the point a future build wires to a real budget-plus-call primitive, and each
was honest that until then the stage it gates falls through rather than being
silently dropped.

That primitive now exists. The daemon's enrichment layer shells out to `claude
-p` synchronously, and the tier table that decides what a job may spend lives in
a file committed to the vault. This module is what those seams ask.

It asks the daemon rather than reading the file, for the same reason the filing
contract is read that way: one parser of a decision, not two. A stage that
carried its own copy of the routing rule would eventually disagree with the
daemon about which jobs are pinned, and the disagreement would show up as money.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

DAEMON_BIN = os.environ.get("AGENTMD", "agentmd")
_TIMEOUT_SECONDS = 20

# The three jobs pinned to the strong tier without audit. Mirrored here only so
# a caller can name them; the daemon is what enforces it, and this list existing
# does not make it true.
PINNED = ("crystallize", "entity-identity-merge", "self-improvement-proposal")


class TierUnavailable(RuntimeError):
    """No tier answer is available.

    Raised rather than defaulted, because every caller of this module is about
    to spend money and the two failure directions are not symmetric. Falling
    back to the cheap tier on an unreachable daemon would spend less and produce
    worse judgments that look exactly like good ones; falling back to strong
    silently would spend more without anybody deciding to. Callers catch this
    and take their own documented fallback, which for both existing seams is to
    skip the stage.
    """


def _ask(args: list) -> object:
    """Run `agentmd tiers --json` and return what it said."""
    binary = shutil.which(DAEMON_BIN) or DAEMON_BIN
    cmd = [binary, "tiers", "--json"] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise TierUnavailable(
            f"no tier answer: {DAEMON_BIN} is not on PATH. The daemon owns the "
            f"qualification table, so without it nothing knows which jobs have "
            f"earned a cheap tier. Set $AGENTMD to a built binary."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TierUnavailable(
            f"no tier answer: {DAEMON_BIN} did not answer within "
            f"{_TIMEOUT_SECONDS}s."
        ) from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "no reason given"
        raise TierUnavailable(f"no tier answer: {detail}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TierUnavailable(
            f"no tier answer: {DAEMON_BIN} returned something that is not JSON"
        ) from exc


def route(job: str, *, cheap: str = "", strong: str = "", pass_version: str = "") -> dict:
    """The tier decision for one job: `{"job", "tier", "why", "model"}`.

    `why` is always populated, including when the answer is the cheap tier. A
    spend line that says "cheap" without saying how it got there is one nobody
    can check, and this whole mechanism exists so tiering is a measurement
    rather than an assumption.
    """
    args = ["--job", job]
    if cheap:
        args += ["--cheap", cheap]
    if strong:
        args += ["--strong", strong]
    if pass_version:
        args += ["--pass-version", pass_version]
    answer = _ask(args)
    if not isinstance(answer, dict) or "tier" not in answer:
        raise TierUnavailable(f"no tier answer for {job}: {answer!r}")
    return answer


def model_for(job: str, **kwargs) -> str:
    """The model name a job should call, or raise if nothing can say."""
    return route(job, **kwargs)["model"]


def cheap_tier_qualified(job: str, **kwargs) -> bool:
    """Whether `job` has earned the cheap tier.

    This is what `dream.cheap_model_tier_available()` was a placeholder for,
    with one difference that matters: the question is per job rather than
    global. A cheap tier is not available or unavailable in general — it is
    qualified for the jobs whose audit earned it and refused for the rest, and
    three jobs refuse it whatever any audit says.
    """
    return route(job, **kwargs)["tier"] == "cheap"


def strong_tier_available(**kwargs) -> bool:
    """Whether a strong-tier call can be made at all.

    This is what `dream_confirm.higher_tier_model_available()` was a placeholder
    for. It is now simply whether the daemon can be reached and names a strong
    model — the primitive it was waiting for exists, so the honest answer is no
    longer a flat `False`.
    """
    try:
        answer = route(PINNED[0], **kwargs)
    except TierUnavailable:
        return False
    return bool(answer.get("model"))


def table(**kwargs) -> list:
    """Every job's routing, for a report a human reads before believing a
    spend line."""
    args = []
    for flag, value in (("--cheap", kwargs.get("cheap")),
                        ("--strong", kwargs.get("strong")),
                        ("--pass-version", kwargs.get("pass_version"))):
        if value:
            args += [flag, value]
    answer = _ask(args)
    if not isinstance(answer, list):
        raise TierUnavailable(f"no tier table: {answer!r}")
    return answer
