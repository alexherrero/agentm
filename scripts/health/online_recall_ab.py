#!/usr/bin/env python3
"""Did a retrieval change help? Judge both sides in one run and compare.

# Why paired, and why it beats standing measurement

The judge drifts about five points across identical runs, which is why the
absolute sufficiency rate resolves to roughly ten points and cannot see most
realistic improvements. But drift is a **common-mode** error: it moves every
verdict in a run the same way. Judge the before-turns and the after-turns in
one run, interleaved, and the drift lands on both arms — so the *difference*
between them is far better resolved than either arm alone.

That is the whole argument for measuring at the moment of a change rather than
continuously. A standing nightly pool compares today's judge against last
month's, which is exactly the comparison drift ruins. This compares two corpora
against one judge in one sitting.

# What it does not fix

The two arms are different traffic, not the same turns re-run. Whatever else
changed between those dates — the work being done, the vault's contents, the
operator's habits — rides along with the retrieval change. The tool reports the
window and the counts so that confound is visible; it cannot remove it.

And it is still the same judge, with no independent human validation. A paired
difference is a better-resolved number of the same kind, not a different kind.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_label_worksheet as bw  # noqa: E402
import recall_traffic  # noqa: E402
import sufficient_context as sc  # noqa: E402

Z95 = 1.959963985
DEFAULT_PER_ARM = 40


def split_at(turns: list, boundary: str) -> tuple:
    """Turns before and after an ISO timestamp boundary."""
    before = [t for t in turns if (t.get("ts") or "") < boundary]
    after = [t for t in turns if (t.get("ts") or "") >= boundary]
    return before, after


def draw(before: list, after: list, *, per_arm: int, seed: int) -> list:
    """A balanced, interleaved judging order.

    Interleaved on purpose. If one arm were judged first and the other second,
    any drift *within* the run would land unevenly and reappear as a difference
    between the arms — manufacturing exactly the effect the tool exists to
    detect.
    """
    rng = random.Random(seed)
    b = sorted(before, key=lambda t: sc.turn_key(t))
    a = sorted(after, key=lambda t: sc.turn_key(t))
    rng.shuffle(b)
    rng.shuffle(a)
    n = min(per_arm, len(b), len(a))
    out = []
    for i in range(n):
        out.append(("before", b[i]))
        out.append(("after", a[i]))
    return out


def paired_difference(k_b: int, n_b: int, k_a: int, n_a: int) -> dict:
    """The change in rate, with an interval on the difference itself.

    Reporting two rates and letting a reader subtract them invites the mistake
    of comparing two intervals that overlap and concluding nothing happened.
    The interval that answers the question is the one on the difference.
    """
    if not n_b or not n_a:
        return {"note": "one arm has no scored turns — nothing to compare"}
    p_b, p_a = k_b / n_b, k_a / n_a
    diff = p_a - p_b
    se = math.sqrt(p_b * (1 - p_b) / n_b + p_a * (1 - p_a) / n_a)
    lo, hi = diff - Z95 * se, diff + Z95 * se
    return {
        "before_rate": round(p_b, 4), "before": f"{k_b}/{n_b}",
        "after_rate": round(p_a, 4), "after": f"{k_a}/{n_a}",
        "difference": round(diff, 4),
        "difference_ci": [round(lo, 4), round(hi, 4)],
        "moved": bool(lo > 0 or hi < 0),
        "note": ("the interval is on the difference, not on either rate. Two "
                 "overlapping rate intervals can still be a real change, and "
                 "reading them separately is how that gets missed."),
        "drift_note": ("both arms were judged in one run by one judge, so the "
                       "~5-point run-to-run drift is common-mode and largely "
                       "cancels here — which it does not for two rates "
                       "measured on different days."),
    }


def run(boundary: str, *, per_arm: int = DEFAULT_PER_ARM, seed: int = 20260831,
        judge: str = "claude", cap: float = 25.0) -> dict:
    everything = [t for t in recall_traffic.iter_injections(with_text=True)
                  if t.get("_prompt") and t.get("_injected")
                  and not bw.is_machine_prompt(t.get("_prompt"))]
    before, after = split_at(everything, boundary)
    order = draw(before, after, per_arm=per_arm, seed=seed)
    if not order:
        return {"note": f"one side of {boundary} has no usable turns "
                        f"({len(before)} before, {len(after)} after)"}

    rows, spent = [], 0.0
    for arm, t in order:
        if spent >= cap:
            break
        r = sc.judge_turn(t, replicates=1, caller=sc.caller_for(judge))
        spent += float(r.get("cost_usd") or 0)
        rows.append({"arm": arm, "verdict": r.get("verdict"),
                     "ts": t.get("ts")})

    def tally(arm):
        s = [r for r in rows if r["arm"] == arm
             and r["verdict"] in ("sufficient", "insufficient")]
        return sum(1 for r in s if r["verdict"] == "sufficient"), len(s)

    k_b, n_b = tally("before")
    k_a, n_a = tally("after")
    out = paired_difference(k_b, n_b, k_a, n_a)
    out.update({
        "boundary": boundary, "judged": len(rows),
        "cost_usd": round(spent, 2),
        "corpus_before": len(before), "corpus_after": len(after),
        "stopped_at_cap": spent >= cap and len(rows) < len(order),
        "confound_note": ("the arms are different traffic, not the same turns "
                          "re-run. Whatever else changed between these dates "
                          "rides along with the retrieval change."),
    })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--boundary", required=True,
                    help="ISO timestamp of the change, e.g. 2026-08-14T00:00:00Z")
    ap.add_argument("--per-arm", type=int, default=DEFAULT_PER_ARM)
    ap.add_argument("--judge", default="claude")
    ap.add_argument("--max-spend", type=float, default=25.0)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args(argv)

    got = run(args.boundary, per_arm=args.per_arm, judge=args.judge,
              cap=args.max_spend)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(got, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(got, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
