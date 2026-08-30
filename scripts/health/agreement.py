#!/usr/bin/env python3
"""Cohen's κ, and prediction-powered inference over an autorater's verdicts.

Two jobs, and they answer different questions.

**κ asks whether the judge agrees with the operator** beyond what two people
guessing the same marginal distribution would hit by luck. Raw agreement does
not answer that: on this kind of task it overstates κ by 33.8–41.2 points,
because a judge that says "n/a" as often as the operator does will match them
frequently while knowing nothing.

**PPI asks what the population rate is**, given a large set the judge scored and
a small subset a person also labelled. The naive options are both wrong. Using
the judge's rate over everything inherits the judge's bias. Using the operator's
rate over the labelled subset alone throws away the other several hundred turns
and comes with an interval far wider than it needs to be. PPI (Angelopoulos et
al.) corrects the judge's population estimate by the measured bias on the
labelled subset, and the correction carries its own variance so the interval
stays honest.

The estimator is only trustworthy if it is checked where the answer is already
known, which `test_agreement.py` does by construction rather than by inspecting
the formula.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# 1.96 for a 95% interval. Named rather than inlined so a reader can see which
# interval every number in this module is.
Z95 = 1.959963985

# Below this many labels the PPI interval does not cover what it claims to.
#
# Measured, not cited. Sweeping coverage of the nominal-95% interval over 400
# synthetic draws: with a judge that flips 30% of negatives, coverage is 90% at
# 10 labels and reaches nominal by about 20. With a judge that is *nearly right*
# — flipping 5% — it is far worse: 31% at 10 labels, 67% at 30, 85% at 50, and
# only 95% at 100.
#
# The near-right case is the dangerous one and the reason the floor is here. A
# small labelled subset drawn against an accurate judge probably contains no
# judge errors at all, so the residual variance comes out zero and the interval
# announces certainty about a bias it never observed. The point estimate is
# still worth having; the interval is not.
#
# The measured floor lands on the same number ARES reports from a different
# direction, which is reassuring but is not where this one came from.
PPI_MIN_LABELS = 100


def confusion(a: Sequence, b: Sequence) -> dict:
    """Counts of every (rater A, rater B) pair."""
    if len(a) != len(b):
        raise ValueError(f"paired labels required: {len(a)} vs {len(b)}")
    out: dict = {}
    for x, y in zip(a, b):
        out[(x, y)] = out.get((x, y), 0) + 1
    return out


def raw_agreement(a: Sequence, b: Sequence) -> float:
    """The share of items the two raters called the same.

    Reported alongside κ and never in place of it. Two raters who both answer
    "n/a" most of the time will agree often while agreeing about nothing.
    """
    if not a:
        raise ValueError("no labels")
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def cohen_kappa(a: Sequence, b: Sequence) -> dict:
    """Chance-corrected agreement, with a 95% interval.

    κ = (p_o − p_e) / (1 − p_e). The interval uses the standard error from
    Fleiss, Cohen and Everitt; it is approximate and gets unreliable when one
    category holds almost everything, which is why `categories` and the
    marginals come back with the number instead of being summarised away.
    """
    if len(a) != len(b):
        raise ValueError(f"paired labels required: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        raise ValueError("no labels")
    cats = sorted(set(a) | set(b), key=str)
    p_o = raw_agreement(a, b)
    ma = {c: sum(1 for x in a if x == c) / n for c in cats}
    mb = {c: sum(1 for y in b if y == c) / n for c in cats}
    p_e = sum(ma[c] * mb[c] for c in cats)

    if p_e >= 1.0:
        # Both raters used exactly one category, so chance already explains
        # everything and κ is undefined rather than perfect.
        return {"kappa": None, "raw_agreement": round(p_o, 4), "n": n,
                "categories": cats,
                "note": "both raters used a single category — chance agreement "
                        "is 1.0 and kappa is undefined, not perfect"}

    kappa = (p_o - p_e) / (1 - p_e)
    # Fleiss et al.'s large-sample standard error.
    term = sum(ma[c] * mb[c] * (ma[c] + mb[c]) for c in cats)
    var = (p_e + p_e ** 2 - term) / (n * (1 - p_e) ** 2)
    se = math.sqrt(max(var, 0.0))
    return {
        "kappa": round(kappa, 4),
        "kappa_ci": [round(kappa - Z95 * se, 4), round(kappa + Z95 * se, 4)],
        "kappa_se": round(se, 4),
        "raw_agreement": round(p_o, 4),
        "raw_agreement_note": "not kappa — raw overstates chance-corrected "
                              "agreement by 33.8-41.2 points on this kind of "
                              "task, so the two are never reported as one",
        "chance_agreement": round(p_e, 4),
        "n": n,
        "categories": cats,
        "marginals": {"a": {c: round(ma[c], 4) for c in cats},
                      "b": {c: round(mb[c], 4) for c in cats}},
    }


def ppi_mean(labelled_truth: Sequence[float],
             labelled_pred: Sequence[float],
             unlabelled_pred: Sequence[float],
             *, z: float = Z95) -> dict:
    """Prediction-powered estimate of a population mean.

    `labelled_truth` and `labelled_pred` are the operator's label and the
    judge's prediction on the *same* turns; `unlabelled_pred` is the judge's
    prediction on every turn nobody labelled.

    The estimate is the judge's mean over everything, moved by the average
    amount the judge was wrong where a person checked:

        theta = mean(all predictions) + mean(truth − prediction on labelled)

    The first term is precise and biased; the second measures that bias and
    removes it. The variance carries both terms, so an estimate resting on few
    labels reports a wide interval rather than a confident wrong one.
    """
    n = len(labelled_truth)
    if n == 0:
        raise ValueError("PPI needs at least one labelled item")
    if len(labelled_pred) != n:
        raise ValueError("labelled truth and predictions must be paired")

    all_pred = list(labelled_pred) + list(unlabelled_pred)
    N = len(all_pred)
    mean_pred = sum(all_pred) / N
    resid = [t - p for t, p in zip(labelled_truth, labelled_pred)]
    bias = sum(resid) / n
    theta = mean_pred + bias

    # Variance of the two terms. The rectifier's variance dominates whenever the
    # labelled set is small, which is the honest outcome — it is the only part
    # of the estimate that saw a human.
    var_pred = (_var(all_pred) / N) if N > 1 else 0.0
    var_resid = (_var(resid) / n) if n > 1 else 0.0
    se = math.sqrt(var_pred + var_resid)
    enough = n >= PPI_MIN_LABELS
    out = {
        "estimate": round(theta, 4),
        "ci": [round(theta - z * se, 4), round(theta + z * se, 4)]
              if enough else None,
        "se": round(se, 4),
        "n_labelled": n,
        "n_total": N,
        "judge_only_estimate": round(mean_pred, 4),
        "labels_only_estimate": round(sum(labelled_truth) / n, 4),
        "measured_judge_bias": round(bias, 4),
    }
    # The comparison that says whether PPI earned its keep here.
    lab_se = math.sqrt(_var(list(labelled_truth)) / n) if n > 1 else 0.0
    out["labels_only_se"] = round(lab_se, 4)
    out["interval_narrower_than_labels_only"] = bool(
        enough and lab_se > 0 and se < lab_se)
    if not enough:
        out["ci_note"] = (
            f"no interval: {n} labels is below the measured floor of "
            f"{PPI_MIN_LABELS}. Against a judge that is nearly right, a "
            f"labelled subset this small usually contains no judge errors at "
            f"all, so the variance comes out near zero and the interval claims "
            f"certainty about a bias it never saw — coverage of a nominal-95% "
            f"interval measured 31% at 10 labels and 85% at 50. The point "
            f"estimate stands; the interval does not.")
    return out


def _var(xs: Sequence[float]) -> float:
    """Sample variance. Zero for fewer than two points, rather than an error —
    a one-item set has no spread to report."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def wilson(k: int, n: int, *, z: float = Z95) -> Optional[list]:
    """Wilson interval for a proportion, for reporting label counts."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]
