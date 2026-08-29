#!/usr/bin/env python3
"""zero_hit_probe — why do 44% of live recalls surface nothing?

Task 1 measured the rate; this measures the mechanism. The hook's term
extractor caps at six terms and the lexical arm conjoins them, so the
hypothesis under test is that P(all terms co-occur in one note) — not ranking,
and not the corpus — is what empties the result set. Registered in
`results/online-v1/RULE-zero-hit.md` before this ran, prediction and all.

The queries are **real**: the term-sets the hook itself recorded in its
transparency line across 675 production injections. Each is re-issued at
k = 1…6 terms across the daemon's three modes, and the sweep reports hit rate
per (mode, k) rather than one blended number.

No model calls. Read-only against the live index — nothing is written to the
vault, and no prompt text is stored or printed.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import statistics
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall_traffic as rt  # noqa: E402

MODES = ("and", "fusion", "hybrid")
TERM_COUNTS = (1, 2, 3, 4, 5, 6)
DEFAULT_K = 5


class ProbeError(Exception):
    """The probe cannot be trusted — controls failed, or the daemon is absent."""


def daemon_binary() -> str:
    return os.environ.get("AGENTMD", "").strip() or "agentmd"


def search(binary: str, terms: str, mode: str, k: int = DEFAULT_K) -> list:
    """One query, terms only — no `-question`, so the dense arm gets no help.

    Deliberate: this isolates the lexical conjunction under test. The hook does
    pass the question, which is why absolute rates here are lower than
    production and are never reported as the production rate.
    """
    argv = [binary, "search", "-json", "-k", str(k), "-mode", mode, terms]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise ProbeError(f"search failed ({mode}, {terms[:40]!r}): "
                         f"{(proc.stderr or '').strip()[:160]}")
    payload = json.loads(proc.stdout or "{}")
    return [r.get("path", "") for r in (payload.get("results") or [])]


def check_control(binary: str, note_rel: str, terms: str) -> None:
    """Terms drawn from one known note must return that note.

    Without this a flat zero everywhere reads as a finding when it is a dead
    index — the failure mode the offline arc shipped twice before the canary
    existed.
    """
    got = search(binary, terms, "and", k=20)
    if note_rel not in got:
        raise ProbeError(
            f"positive control failed: terms taken verbatim from {note_rel} did "
            f"not return it (got {got[:3] or 'nothing'}). The probe is not "
            "measuring retrieval; no number from this run counts.")


def pick_control(binary: str) -> tuple:
    """Find a note and a term-set drawn from it, for the control."""
    vault = pathlib.Path(os.environ.get("MEMORY_VAULT_PATH")
                         or "/Users/alex/Vault/Agent")
    for p in sorted((vault / "memory").rglob("*.md"))[:400]:
        rel = f"Agent/{p.relative_to(vault).as_posix()}"
        words = [w for w in p.read_text(encoding="utf-8", errors="replace").split()
                 if w.isalpha() and len(w) > 6]
        if len(words) >= 3:
            return rel, " ".join(words[:3])
    raise ProbeError("no suitable control note found in the corpus")


def term_sets(limit: int = 0) -> list:
    """Real extracted term-sets from production injections."""
    seen, out = set(), []
    for inj in rt.iter_injections():
        t = (inj.get("terms") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t.split())
        if limit and len(out) >= limit:
            break
    return out


def sweep(binary: str, sets: list) -> dict:
    """Hit rate per (mode, term count), over real term-sets."""
    grid = {m: {k: {"queried": 0, "empty": 0, "hits": []} for k in TERM_COUNTS}
            for m in MODES}
    for terms in sets:
        for k in TERM_COUNTS:
            if len(terms) < k:
                continue
            q = " ".join(terms[:k])
            for mode in MODES:
                cell = grid[mode][k]
                got = search(binary, q, mode)
                cell["queried"] += 1
                cell["hits"].append(len(got))
                if not got:
                    cell["empty"] += 1
    return grid


def report(grid: dict) -> dict:
    out = {}
    for mode, byk in grid.items():
        rows = {}
        for k, cell in byk.items():
            if not cell["queried"]:
                continue
            rows[k] = {
                "queried": cell["queried"],
                "empty": cell["empty"],
                "zero_hit_rate": round(cell["empty"] / cell["queried"], 4),
                "mean_hits": round(statistics.mean(cell["hits"]), 2),
            }
        out[mode] = rows
    return out


def check_monotone(rows: dict) -> str:
    """Does zero-hit fall as terms are dropped? The registered prediction.

    Three answers, not two. An all-equal series is *trivially* monotone, and
    the first run of this probe returned exactly that — 0% at every term count
    — which printed as HOLDS and meant nothing. A prediction that cannot fail
    on the data in front of it has not been tested, so that case reports
    `no-variation` and is read as an absent signal rather than a confirmation.
    """
    ks = sorted(rows)
    rates = [rows[k]["zero_hit_rate"] for k in ks]
    if not rates or max(rates) - min(rates) < 1e-9:
        return "no-variation"
    return "holds" if all(a <= b + 1e-9 for a, b in zip(rates, rates[1:])) else "fails"


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=60,
                    help="distinct term-sets to sweep (0 = all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    binary = daemon_binary()
    try:
        rel, ctrl_terms = pick_control(binary)
        check_control(binary, rel, ctrl_terms)
        sets = term_sets(args.limit)
        if not sets:
            raise ProbeError("no real term-sets found in the transcripts")
        grid = report(sweep(binary, sets))
    except ProbeError as exc:
        print(f"zero-hit-probe: {exc}", file=sys.stderr)
        return 2

    monotone = {m: check_monotone(rows) for m, rows in grid.items() if rows}
    out = {"term_sets": len(sets), "control_note": rel,
           "grid": grid, "monotone": monotone}

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    print(f"zero-hit probe — {len(sets)} real term-sets, control OK ({rel.split('/')[-1]})")
    print("\nzero-hit rate by mode and term count "
          "(terms only, no question — lower is better)\n")
    header = "  mode      " + "".join(f"  k={k}  " for k in TERM_COUNTS)
    print(header)
    for mode, rows in grid.items():
        cells = "".join(
            f"  {rows[k]['zero_hit_rate']:.0%}  " if k in rows else "   —   "
            for k in TERM_COUNTS)
        print(f"  {mode:9s}{cells}")
    print("\nmean hits returned\n")
    print(header)
    for mode, rows in grid.items():
        cells = "".join(
            f"  {rows[k]['mean_hits']:4.1f} " if k in rows else "   —   "
            for k in TERM_COUNTS)
        print(f"  {mode:9s}{cells}")
    print("\nprediction — zero-hit falls monotonically as terms drop:")
    for mode, verdict in monotone.items():
        gloss = {"no-variation": "NO VARIATION — the prediction was untestable here",
                 "holds": "HOLDS", "fails": "FAILS"}[verdict]
        print(f"  {mode:9s} {gloss}")

    print("\nthe rate over time (the ledger, all recalls)\n")
    for label, lo, hi, n, z in history_bands():
        print(f"  {label:28s} n={n:5d}  zero {z / n:5.1%}" if n else
              f"  {label:28s} no data")
    return 0


def history_bands() -> list:
    """Zero-hit rate before and after the hybrid-retrieval ladder.

    The term-count sweep found no signal; this is where the answer was. The
    ladder merged 2026-08-14, and the rate breaks there. Reported as bands
    rather than a single lifetime average, because a lifetime average over a
    system that changed under it is a number about two different systems.
    """
    rows = list(rt.iter_ledger())
    out = []
    for label, lo, hi in (("before ladder (to 08-13)", "2026-01-01", "2026-08-13"),
                          ("after ladder (08-15 on)", "2026-08-15", "2999-01-01"),
                          ("last 7 days", "2026-08-23", "2999-01-01")):
        sub = [r for r in rows if lo <= (r.get("ts") or "")[:10] <= hi]
        out.append((label, lo, hi, len(sub),
                    sum(1 for r in sub if not r.get("hit_count"))))
    return out


if __name__ == "__main__":
    sys.exit(main())
