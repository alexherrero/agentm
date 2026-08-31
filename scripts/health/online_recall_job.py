#!/usr/bin/env python3
"""The nightly online-recall pass: judge what is new, and accumulate.

# Why this accumulates rather than re-measures

At the sample this arc has, the always-valid interval spans more than ±0.25 and
excludes nothing. About 45 judged turns clear that bar, 126 reach ±0.15, and 274
reach ±0.10. Re-judging the same turns every night would burn money to learn
nothing; judging *new* ones grows the sample until the number is worth reading.

So each run picks up turns nobody has judged yet, judges as many as the night's
budget allows, and appends. The pool is the measurement; a single night is a
contribution to it.

# Why it is capped, and what the cap costs

Judging bills about $0.20 a turn. Unattended and uncapped over a corpus that
grows daily, that is a three-figure monthly bill for a number nobody reads
daily. The default cap of $2 buys roughly ten turns a night, which reaches a
±0.10 interval in about a month. Raising it buys the number sooner and nothing
else.

# What it will not do

It does not re-judge. A turn's verdict is written once and stays, because a
verdict that changed on re-run would make the pool a mixture of measurements
taken at different times by an instrument known to drift five points. The drift
is reported separately rather than smeared through the history.

It does not delete. A pool that shrinks when traffic ages out would make the
interval narrow and widen for reasons that have nothing to do with recall.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_label_worksheet as bw  # noqa: E402
import recall_traffic  # noqa: E402
import sufficient_context as sc  # noqa: E402

DEFAULT_CAP_USD = 2.0
POOL_NAME = "online-recall-pool.json"


def pool_path(vault: pathlib.Path) -> pathlib.Path:
    return vault / "desk" / "labelling" / POOL_NAME


def load_pool(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"rows": [], "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def unjudged(turns: list, pool: dict) -> list:
    """Turns with no verdict yet, keyed on the sampler's own key.

    Keyed on session+timestamp rather than the prompt hash: 32 hashes cover 83
    turns in this corpus, so a hash-keyed check would skip turns it had never
    actually judged.
    """
    seen = {r.get("turn_key") for r in pool.get("rows", [])}
    return [t for t in turns if sc.turn_key(t) not in seen]


def run(vault: pathlib.Path, *, cap: float = DEFAULT_CAP_USD,
        judge: str = "claude", now: float = None) -> dict:
    """One night's contribution to the pool."""
    out = pool_path(vault)
    pool = load_pool(out)

    everything = [t for t in recall_traffic.iter_injections(with_text=True)
                  if t.get("_prompt") and t.get("_injected")
                  and not bw.is_machine_prompt(t.get("_prompt"))]
    todo = unjudged(everything, pool)

    spent, judged = 0.0, []
    for t in todo:
        if spent >= cap:
            break
        row = sc.judge_turn(t, replicates=1, caller=sc.caller_for(judge))
        spent += float(row.get("cost_usd") or 0)
        row["arm"] = t.get("arm")
        row["ts"] = t.get("ts")
        row.pop("_missing", None)
        judged.append(row)

    pool["rows"] = pool.get("rows", []) + judged
    pool["runs"] = pool.get("runs", []) + [{
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(now or time.time())),
        "judged": len(judged), "cost_usd": round(spent, 2),
        "corpus": len(everything), "unjudged_remaining": len(todo) - len(judged),
    }]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pool, indent=2) + "\n", encoding="utf-8")

    scored = [r for r in pool["rows"]
              if r.get("verdict") in ("sufficient", "insufficient")]
    return {
        "judged_tonight": len(judged),
        "cost_usd": round(spent, 2),
        "pool_total": len(pool["rows"]),
        "pool_scored": len(scored),
        "unjudged_remaining": len(todo) - len(judged),
        "pool": str(out),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-spend", type=float, default=DEFAULT_CAP_USD)
    ap.add_argument("--judge", default="claude")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import harness_memory
        vault = pathlib.Path(harness_memory.vault_path())
    except Exception as exc:  # noqa: BLE001
        # Graceful-skip, like every other scheduled job here: a missing vault
        # is a machine without this feature, not a failure worth alarming on.
        print(f"online-recall: no vault resolved ({exc}) — skipping",
              file=sys.stderr)
        return 0

    got = run(vault, cap=args.max_spend, judge=args.judge)
    if args.json:
        print(json.dumps(got, indent=2))
    else:
        print(f"online-recall: judged {got['judged_tonight']} new turns "
              f"(${got['cost_usd']:.2f}), pool now {got['pool_total']} "
              f"({got['pool_scored']} scored), "
              f"{got['unjudged_remaining']} still unjudged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
