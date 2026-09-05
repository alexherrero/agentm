#!/usr/bin/env python3
"""eval_lifecycle_separation.py — measure what the lifecycle axis does to rank.

Filing v2 part 6 (task 1). The design says a dormant note ranks below its
active twin and an archived note leaves everyday search; the plan says the
parameters are tuned by measurement, not guessed. This harness is the
measurement: it builds a scratch vault of twin pairs — the same words, one
twin `lifecycle: dormant` (or `archived`), the other `active` — indexes it
with the shipped `agentmd`, queries each pair's own words, and records where
the two twins landed. Nothing here computes an expected rank; the daemon
ranks, this script reads.

What it reports, with n:

* **dormant pairs** — how many of n pairs put the dormant twin below its
  active twin (the demotion), ties, and a two-sided exact sign test against
  "the order is a coin flip". The dormant twin's path sorts *first*, so a
  tie-break would put it on top: every "below" is the multiplier, not luck.
* **archived pairs** — how many of m archived twins are absent from the
  everyday query (`archived_hidden` on the outcome), and how many come back
  demoted on the explicit archive query (`-include-archived`).
* **control** — the same pairs with the axis removed from the "a" twin. The
  instrument must then see path order (a-twin first) in every pair; if it
  does not, the fixture is not testing what it claims.

The dense arm is not exercised — the scratch index carries no vectors — and
that is stated rather than hidden: the wall and the multiplier run in the
same code path for both arms (`index.wallArchived`, `penalizeRankAndDecay`),
and the daemon's own tests pin the dense arm (`TestTheWallReachesTheDenseArm`).
This harness measures the lexical ordering, which is where the twins meet.

Exit 0 when every dormant twin is below and every archived twin is hidden;
1 otherwise; 2 when the daemon binary is missing (the caller decides whether
that is a skip). `--json` prints the summary; `--pairs` sets n.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Distinct vocabularies per pair, so one pair's query never hits another's
# notes. Three rare-ish words each; the sentence carries all three.
_TOPICS = [
    ("kestrel", "gantry", "quorum"), ("lantern", "bobbin", "estuary"), ("mortise", "cadence", "yarrow"),
    ("pewter", "isthmus", "gimbal"), ("sextant", "ballast", "lichen"), ("tannin", "spindle", "harrow"),
    ("verdigris", "cistern", "mallet"), ("wicket", "dowel", "furrow"), ("zither", "grommet", "tundra"),
    ("abacus", "hummock", "trellis"), ("brine", "pinion", "sedge"), ("cobalt", "skiff", "thistle"),
    ("damask", "quoin", "vellum"), ("ember", "rivet", "wold"), ("fennel", "tiller", "cairn"),
    ("gable", "umber", "kiln"), ("heron", "vane", "alder"), ("ingot", "wharf", "bramble"),
    ("jetty", "awl", "coppice"), ("kelp", "bevel", "drumlin"), ("loam", "chisel", "eyrie"),
    ("marl", "dovetail", "firth"), ("nettle", "ferrule", "gorse"), ("ochre", "gudgeon", "heath"),
    ("plinth", "hasp", "inlet"), ("quill", "joist", "knoll"), ("rowan", "kerf", "lea"),
    ("saffron", "lathe", "mere"), ("teak", "mandrel", "nook"), ("umbel", "newel", "orchard"),
    ("vetch", "oakum", "paddock"), ("willow", "pintle", "quay"),
]


def _sentence(words: tuple) -> str:
    a, b, c = words
    return (f"The {a} sits on the {b} until the {c} agrees, and the note records "
            f"why the {a} was moved there in the first place.\n")


def _write(vault: Path, rel: str, title: str, lifecycle: str | None, body: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntitle: {title}\nkind: reference\nstatus: active\n"
    if lifecycle:
        fm += f"lifecycle: {lifecycle}\n"
    p.write_text(fm + "---\n\n" + body, encoding="utf-8")


def build_fixture(vault: Path, *, pairs: int, archived: int, control: bool) -> dict:
    """The twin corpus. Returns the pair table: {pair_id: (a_rel, b_rel, words)}."""
    if pairs + archived > len(_TOPICS):
        raise SystemExit(f"at most {len(_TOPICS)} pairs in total (asked {pairs} + {archived})")
    table = {"dormant": [], "archived": []}
    for i in range(pairs):
        words = _TOPICS[i]
        a = f"memory/semantic/d{i:02d}-a-dormant.md"
        b = f"memory/semantic/d{i:02d}-b-active.md"
        _write(vault, a, f"Pair {i} a", None if control else "dormant", _sentence(words))
        _write(vault, b, f"Pair {i} b", "active", _sentence(words))
        table["dormant"].append((a, b, words))
    for j in range(archived):
        words = _TOPICS[pairs + j]
        a = f"memory/semantic/r{j:02d}-a-archived.md"
        b = f"memory/semantic/r{j:02d}-b-active.md"
        _write(vault, a, f"Archived {j} a", None if control else "archived", _sentence(words))
        _write(vault, b, f"Archived {j} b", "active", _sentence(words))
        table["archived"].append((a, b, words))
    return table


class Daemon:
    """One scratch daemon: its own kernel config, index and state dir, so the
    live vault is never touched and the run is reproducible."""

    def __init__(self, binary: str, work: Path, vault: Path):
        self.binary = binary
        self.config = work / "agentm-config.json"
        self.index = work / "index.db"
        self.state = work / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({
            "plugins.obsidian-vault.vault_path": str(vault),
            "daemon.index_path": str(self.index),
        }), encoding="utf-8")
        self.env = dict(os.environ, AGENTM_STATE_DIR=str(self.state))
        (vault / "standards").mkdir(exist_ok=True)
        self._run("rules", "--init", str(vault / "standards" / "storage-rules.md"))

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        argv = [self.binary, args[0], "--config", str(self.config), *args[1:]]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=self.env)
        if proc.returncode != 0:
            raise RuntimeError(f"{' '.join(argv[:2])} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
        return proc

    def reindex(self) -> str:
        return self._run("reindex").stdout.strip()

    def search(self, terms: str, *, include_archived: bool = False, k: int = 10) -> dict:
        args = ["search", "-json", "-k", str(k)]
        if include_archived:
            args.append("-include-archived")
        args.append(terms)
        return json.loads(self._run(*args).stdout or "{}")


def _ranks(outcome: dict) -> dict:
    return {row.get("path", ""): i + 1 for i, row in enumerate(outcome.get("results") or [])}


def sign_test_two_sided(k: int, n: int) -> float:
    """P(X <= min(k, n-k) or X >= max(k, n-k)) for X ~ Binomial(n, 1/2)."""
    if n == 0:
        return 1.0
    lo = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(0, lo + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def measure(binary: str, *, pairs: int, archived: int, keep: bool = False) -> dict:
    work = Path(tempfile.mkdtemp(prefix="lifecycle-separation-"))
    try:
        out = {"n_dormant": pairs, "n_archived": archived, "binary": binary}
        for arm in ("measured", "control"):
            vault = work / arm / "vault"
            (vault / "memory" / "semantic").mkdir(parents=True)
            table = build_fixture(vault, pairs=pairs, archived=archived, control=(arm == "control"))
            d = Daemon(binary, work / arm, vault)
            d.reindex()
            rows = []
            below = ties = missing = 0
            for a, b, words in table["dormant"]:
                r = _ranks(d.search(" ".join(words)))
                ra, rb = r.get(a), r.get(b)
                rows.append({"pair": Path(a).name[:3], "a": ra, "b": rb})
                if ra is None or rb is None:
                    missing += 1
                elif ra > rb:
                    below += 1
                elif ra == rb:
                    ties += 1
            hidden = present_back = below_back = 0
            for a, b, words in table["archived"]:
                everyday = d.search(" ".join(words))
                if a not in _ranks(everyday) and everyday.get("archived_hidden", 0) >= 1:
                    hidden += 1
                explicit = _ranks(d.search(" ".join(words), include_archived=True))
                if a in explicit:
                    present_back += 1
                    if explicit.get(b) is not None and explicit[a] > explicit[b]:
                        below_back += 1
            a_first = sum(1 for x in rows if x["a"] is not None and x["b"] is not None and x["a"] < x["b"])
            out[arm] = {
                "dormant_below_active": below, "ties": ties, "missing": missing, "a_first": a_first,
                "p_two_sided": sign_test_two_sided(below, pairs) if pairs else None,
                "archived_hidden_everyday": hidden, "archived_back_on_explicit": present_back,
                "archived_below_active_on_explicit": below_back,
                "pairs": rows,
            }
        m, c = out["measured"], out["control"]
        out["verdict"] = {
            "demotion": m["dormant_below_active"] == pairs and m["missing"] == 0,
            "wall": m["archived_hidden_everyday"] == archived and m["archived_back_on_explicit"] == archived
                    and m["archived_below_active_on_explicit"] == archived,
            "instrument": c["a_first"] == pairs and c["archived_hidden_everyday"] == 0
                          and c["archived_back_on_explicit"] == archived,
        }
        out["pass"] = all(out["verdict"].values())
        if keep:
            out["kept"] = str(work)
        return out
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def render(out: dict) -> str:
    m, c, v = out["measured"], out["control"], out["verdict"]
    n, a = out["n_dormant"], out["n_archived"]
    lines = [
        f"lifecycle separation — {n} dormant/active pairs, {a} archived/active pairs, lexical arm, {Path(out['binary']).name}",
        f"  dormant below active: {m['dormant_below_active']}/{n} (ties {m['ties']}, missing {m['missing']}), "
        f"two-sided sign test p = {m['p_two_sided']:.2e}" if m["p_two_sided"] is not None else "  no dormant pairs",
        f"  archived hidden on the everyday query: {m['archived_hidden_everyday']}/{a}; back on the explicit query: "
        f"{m['archived_back_on_explicit']}/{a}, of which below the active twin: {m['archived_below_active_on_explicit']}/{a}",
        f"  control (axis removed): a-twin first by path in {c['a_first']}/{n}; archived hidden {c['archived_hidden_everyday']}/{a} (expect 0)",
        f"  verdict: demotion {'PASS' if v['demotion'] else 'FAIL'} · wall {'PASS' if v['wall'] else 'FAIL'} · "
        f"instrument {'PASS' if v['instrument'] else 'FAIL'}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="measure the lifecycle axis's effect on rank against a scratch daemon")
    p.add_argument("--agentmd", default=os.environ.get("AGENTMD") or shutil.which("agentmd"),
                   help="the daemon binary (default: $AGENTMD, then PATH)")
    p.add_argument("--pairs", type=int, default=24, help="dormant/active twin pairs (n)")
    p.add_argument("--archived", type=int, default=8, help="archived/active twin pairs")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.add_argument("--keep", action="store_true", help="keep the scratch vaults and print their path")
    args = p.parse_args(argv)
    if not args.agentmd or not Path(args.agentmd).exists():
        print("eval_lifecycle_separation: no agentmd binary (set $AGENTMD or --agentmd)", file=sys.stderr)
        return 2
    out = measure(args.agentmd, pairs=args.pairs, archived=args.archived, keep=args.keep)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(render(out))
        if out.get("kept"):
            print(f"  scratch kept at {out['kept']}")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
