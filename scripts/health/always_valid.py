#!/usr/bin/env python3
"""Intervals that survive being looked at.

A 95% confidence interval promises to cover the truth 95% of the time **for one
pre-specified sample size**. Check it after every run and stop when it looks
interesting and the promise is void — the error rate compounds across every
peek. This stream trickles in a few dozen turns at a time and will be read
whenever someone wonders how recall is doing, which is exactly the pattern that
breaks a fixed-horizon interval.

A confidence sequence holds at every sample size at once. It is wider, and the
width is what the guarantee costs.

# What it still does not cover

The sequence assumes each observation is a draw from a fixed distribution. This
judge is not fixed: three runs over the identical 90 turns gave 8.0%, 13.0% and
12.2%, a five-point spread from re-running alone. No interval over one run's
observations can absorb that, and widening one to pretend otherwise would be
inventing a guarantee.

So drift is reported as its own band, measured rather than modelled, and the
two are printed side by side. A reader gets a statistical interval that is
honest about sampling and a drift figure that is honest about the instrument,
rather than one number quietly carrying both jobs badly.
"""
from __future__ import annotations

import math
from typing import Optional

# Hoeffding's sub-Gaussian parameter for an observation bounded in [0, 1].
SIGMA = 0.5

# Tunes where the boundary is tightest, and it has to be fixed in advance: the
# guarantee is for a rho chosen before the data, so picking one that flattered
# an observed number would void it.
#
# Measured, not reasoned. The first value here was 1/300, from a
# half-remembered "set it near 1/n" heuristic, and it was about forty times too
# small — radius +/-1.116 at n=43 and +/-0.178 at n=300, where a swept rho
# gives +/-0.250 and +/-0.095. The optimum sits near 0.125 for n=300, which is
# where the schedule should settle.
DEFAULT_RHO = 0.125
RHO_TUNED_FOR_N = 300

# Above this the interval spans so much of the unit line that it excludes
# nothing, and printing it would dress "not enough data yet" as a measurement —
# the failure the scorecard's own never-fabricate rule exists to stop. Roughly
# 45 turns clear it, 126 reach +/-0.15, and 274 reach +/-0.10.
UNINFORMATIVE_RADIUS = 0.25

# Measured, not assumed: three runs of the same judge over the same 90 turns
# gave 8.0%, 13.0% and 12.2%. Re-measure when the judge, its prompt, or its
# model changes; the number is a property of that particular arrangement.
DRIFT_POINTS = 0.05
DRIFT_RUNS = 3
DRIFT_OBSERVED = (0.080, 0.130, 0.122)


def sequence_radius(n: int, *, alpha: float = 0.05,
                    rho: float = DEFAULT_RHO, sigma: float = SIGMA
                    ) -> Optional[float]:
    """Half-width of an always-valid interval for a mean, at sample size n.

    Howard et al.'s normal-mixture boundary for a sub-Gaussian process. Unlike a
    fixed-horizon radius this one holds for every n at once, so the interval may
    be recomputed and read as often as anyone likes without spending error
    budget on each look.

    Two-sided, so the tail mass is split before the boundary is computed.
    """
    if n <= 0:
        return None
    a = alpha / 2.0
    v = n * sigma * sigma
    inner = math.sqrt(v * rho + 1.0)
    # Guard the degenerate case where the log argument dips to or below 1.
    lg = math.log(max(inner / a, 1.0 + 1e-12))
    return math.sqrt(2.0 * (v * rho + 1.0) / rho * lg) / n


def sequence_interval(k: int, n: int, *, alpha: float = 0.05,
                      rho: float = DEFAULT_RHO) -> Optional[list]:
    """An always-valid interval for k successes in n, clipped to [0, 1]."""
    r = sequence_radius(n, alpha=alpha, rho=rho)
    if r is None:
        return None
    p = k / n
    return [round(max(0.0, p - r), 4), round(min(1.0, p + r), 4)]


def report(k: int, n: int, *, alpha: float = 0.05,
           drift: float = DRIFT_POINTS) -> dict:
    """The rate, its always-valid interval, and the instrument's own drift.

    The drift band is not folded into the interval. They answer different
    questions — one is "how much could sampling alone move this", the other is
    "how much does this judge move when nothing at all changes" — and a single
    merged number would let a reader take the second for the first.
    """
    if n <= 0:
        return {"n": 0,
                "note": "no observations — a rate here would be a statement "
                        "about the absence of data, not about recall"}
    p = k / n
    ci = sequence_interval(k, n, alpha=alpha)
    radius = sequence_radius(n, alpha=alpha)
    out = {
        "successes": k, "n": n, "rate": round(p, 4),
        "always_valid_ci": ci,
        "always_valid_radius": round(radius, 4) if radius else None,
        "alpha": alpha,
        "ci_note": ("always-valid: holds at every sample size at once, so it "
                    "may be read after any run without inflating the error "
                    "rate. Wider than a fixed-horizon interval, and that width "
                    "is what the guarantee costs."),
    }
    if radius and radius > UNINFORMATIVE_RADIUS:
        # Wide enough to exclude nothing. Saying so *is* the measurement; the
        # interval on its own would read as one without being one.
        out["ci_uninformative"] = True
        out["ci_note"] = (
            f"the always-valid interval spans +/-{radius:.2f} at n={n}, which "
            f"excludes nothing worth excluding. That is not a finding about "
            f"recall — it is the sample being too small to support a claim "
            f"that survives repeated reading. About 45 turns clear this bar, "
            f"126 reach +/-0.15, and 274 reach +/-0.10.")
    if drift:
        out["drift_points"] = drift
        out["drift_band"] = [round(max(0.0, p - drift), 4),
                             round(min(1.0, p + drift), 4)]
        out["drift_note"] = (
            f"measured over {DRIFT_RUNS} runs of the same judge on the same "
            f"turns: {', '.join(f'{x:.1%}' for x in DRIFT_OBSERVED)}. Not "
            f"folded into the interval above — that one is about sampling, "
            f"this one is about the instrument, and merging them would let "
            f"either be mistaken for the other.")
        # What a reader actually needs: the widest defensible range.
        lo = min(out["drift_band"][0], ci[0]) if ci else out["drift_band"][0]
        hi = max(out["drift_band"][1], ci[1]) if ci else out["drift_band"][1]
        out["honest_range"] = [round(lo, 4), round(hi, 4)]
    return out


def fixed_horizon_radius(k: int, n: int, *, alpha: float = 0.05) -> float:
    """A textbook Wald radius, provided only so the two can be compared.

    Not for reporting. It is here because the difference between the two is the
    argument for the sequence, and a reader who cannot see the difference has
    to take the argument on faith.
    """
    if n <= 0:
        return 0.0
    p = k / n
    z = 1.959963985 if abs(alpha - 0.05) < 1e-9 else 1.959963985
    return z * math.sqrt(max(p * (1 - p), 1e-12) / n)
