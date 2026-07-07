#!/usr/bin/env python3
"""eval_v6_graph.py — V6-20 eval: graph.py's edge-P/R against the frozen fixture.

PLAN-wave-e-v6-index task 4 verification: "V6-20 eval against task 2's
frozen edge fixture, reporting edge-P/R as a first-class metric... the
merge gate is this eval moving the recall axes."

This is the *real-vault* half of that verification — it reads the actual
files edge-fixture-v0.json's entries point at (a real, private Obsidian
vault, not a repo-local fixture), runs graph.py's extractor against each,
and reports precision/recall scoped to exactly the (source, target) pairs
the fixture labels (the fixture samples a subset of each file's real edges,
not a full enumeration — scoring against the full extracted set would
wrongly penalize the extractor for finding real edges nobody happened to
label).

Graceful-skip contract (mirrors recall.py/save.py's vault resolution):
CI has no access to the operator's private vault, so this script is NOT a
hard check-all.sh gate — it resolves MEMORY_VAULT_PATH (or --vault-path),
and if the vault or any fixture-referenced file is unreachable, it reports
what it can and exits 0 without failing the battery. The synthetic unit
tests in scripts/test_graph_extract.py are what check-all.sh actually
gates on; this script is the honest, best-effort real-corpus measurement,
run manually or from run-fast-tier.sh (graceful-skip, contributes a
check-record only when the vault is actually reachable).

Usage:
    python3 scripts/health/eval_v6_graph.py [--vault-path PATH]
                                             [--fixture PATH] [--jsonl-out PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_MEMORY_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

_DEFAULT_FIXTURE = _REPO / "scripts" / "health" / "fixtures" / "v6-eval" / "edge-fixture-v0.json"

HEALTH_SUITE = "eval-v6-graph"
HEALTH_AXIS = "memory persist+recall"


def _resolve_vault(arg_vault_path: str | None) -> Path | None:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return None


def _emit_jsonl(jsonl_out: str | None, check: str, passed: bool, weight: float = 1.0) -> None:
    if not jsonl_out:
        return
    record = {"suite": HEALTH_SUITE, "axis": HEALTH_AXIS, "check": check, "pass": passed, "weight": weight}
    with open(jsonl_out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_eval(vault: Path, fixture_path: Path, *, stderr=sys.stderr) -> dict:
    import graph

    doc = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = doc["entries"]

    # Group fixture-labeled (source, target) pairs by source file, so each
    # file is only read once and each extraction is scored against exactly
    # the pairs the fixture names for that file.
    by_source: dict[str, list[dict]] = {}
    for e in entries:
        by_source.setdefault(e["source_path"], []).append(e)

    tp = fn = 0          # true edges: found (any type) / missed entirely
    tp_type_correct = 0  # of tp, how many got the exact right edge_type
    trap_tn = trap_fp = 0  # negative/trap entries: correctly rejected / leaked through
    files_missing = 0
    files_read = 0

    for source_path, labeled in by_source.items():
        full_path = vault / source_path
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            files_missing += 1
            continue
        files_read += 1
        extracted = graph.extract_edges(source_path, content)
        extracted_by_target: dict[str, str] = {}
        for e in extracted:
            extracted_by_target.setdefault(e.target, e.edge_type)

        for row in labeled:
            target = row["target"]
            found = target in extracted_by_target
            if row["is_edge"]:
                if found:
                    tp += 1
                    if extracted_by_target[target] == row["edge_type"]:
                        tp_type_correct += 1
                else:
                    fn += 1
            else:
                if found:
                    trap_fp += 1
                else:
                    trap_tn += 1

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    type_precision = tp_type_correct / tp if tp else 0.0
    trap_rejection_rate = trap_tn / (trap_tn + trap_fp) if (trap_tn + trap_fp) else 1.0

    return {
        "files_read": files_read,
        "files_missing": files_missing,
        "true_edges_total": tp + fn,
        "true_edges_found": tp,
        "true_edges_missed": fn,
        "recall": recall,
        "found_edges_correct_type": tp_type_correct,
        "type_precision_on_found": type_precision,
        "trap_entries_total": trap_tn + trap_fp,
        "trap_correctly_rejected": trap_tn,
        "trap_leaked_as_edges": trap_fp,
        "trap_rejection_rate": trap_rejection_rate,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vault-path", default=None)
    p.add_argument("--fixture", default=str(_DEFAULT_FIXTURE))
    p.add_argument("--jsonl-out", default=None)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    vault = _resolve_vault(args.vault_path)
    if vault is None or not vault.exists():
        print(
            "[eval-v6-graph] no reachable vault (MEMORY_VAULT_PATH unset or "
            "path missing) — skipping the real-corpus eval (graceful-skip; "
            "this is expected in CI, which has no access to the private "
            "vault). The synthetic unit tests in test_graph_extract.py "
            "are what check-all.sh actually gates on.",
            file=sys.stderr,
        )
        return 0

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"[eval-v6-graph] fixture not found: {fixture_path} — skipping", file=sys.stderr)
        return 0

    result = run_eval(vault, fixture_path)
    print(json.dumps(result, indent=2))

    # Pass bar for this inaugural measurement (no floor was pre-set — this
    # run IS the inaugural measurement FABLE R2 says a future plan's fusion
    # decision floors against): the extractor must correctly reject every
    # deliberate trap (zero tolerated false positives on the negative set —
    # this is the concrete, engineerable bar the fixture's README names as
    # the whole point of including traps) and find a majority of the
    # labeled true edges (recall > 0.5 — a sane floor for a v0 cascade,
    # not a claim of production-readiness).
    passed = result["trap_leaked_as_edges"] == 0 and result["recall"] > 0.5
    _emit_jsonl(
        args.jsonl_out,
        f"V6-2 typed-edge extraction: edge-recall={result['recall']:.3f}, "
        f"trap-rejection={result['trap_rejection_rate']:.3f} "
        f"({result['trap_leaked_as_edges']} leaked / {result['trap_entries_total']} traps)",
        passed,
    )
    if not passed:
        print(
            "[eval-v6-graph] WARNING: below the inaugural pass bar "
            "(0 tolerated trap leaks + recall > 0.5) — see output above.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
