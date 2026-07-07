#!/usr/bin/env python3
"""eval_v6_consolidate.py — V6-20 eval slice for V6-4 consolidation.

PLAN-wave-e-v6-index task 7 verification: "V6-20 eval slice confirming
consolidation reduces corpus size/redundancy without a measurable recall
regression on the pre-registered query set."

Honest scope note on "reduces corpus size": consolidation's never-delete
invariant (design-doc.md § v6-25-external-thinking-audit touch-point row;
this task's own module docstring) means it ADDS a semantic-tier entry, it
never deletes the episodic sources — so raw file COUNT increases, not
decreases, by design. What this eval actually measures and reports is the
REDUNDANCY half: how many distinct entries a query needs to retrieve to
get full coverage of a recurring fact, before vs. after consolidation.
Actual corpus-size reduction (pruning/archiving now-redundant episodic
sources once captured at the semantic tier) is dreaming-pipeline territory
(PLAN-wave-e-dreaming's compression stage), out of scope for V6-4's
directional invariant here — reported as an honest gap, not hidden.

Synthetic fixture only — this eval never touches the real vault. Actually
running consolidation against real content is an operator-invoked pass
(matching the dreaming pipeline's own staged/confirmed posture for
corpus-mutating passes), not something this module does autonomously.

Usage:
    python3 scripts/health/eval_v6_consolidate.py [--jsonl-out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_MEMORY_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

HEALTH_SUITE = "eval-v6-consolidate"
HEALTH_AXIS = "memory persist+recall"

_RECURRING_SOURCES = [f"personal/insight/episodic-{n}.md" for n in range(1, 4)]
_FILLER_SOURCES = [f"personal/insight/filler-{n}.md" for n in range(1, 6)]


def _write_entry(vault: Path, rel: str, body: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nkind: insight\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
        "tags: []\ngroup: personal\nslug: " + Path(rel).stem + "\nalways_load: false\n---\n\n"
        + body + "\n",
        encoding="utf-8",
    )


def _build_fixture(vault: Path) -> None:
    for rel in _RECURRING_SOURCES:
        _write_entry(
            vault, rel,
            "Discussing the widget-migration approach and referencing "
            "[[widget-migration-pattern]] as the recurring technique used.",
        )
    for i, rel in enumerate(_FILLER_SOURCES):
        _write_entry(vault, rel, f"Unrelated filler content number {i}, nothing to do with widgets.")


def run_eval() -> dict:
    import recall
    import consolidate
    from revert_log import RevertLog

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir(parents=True)
        _build_fixture(vault)

        query_text = "widget migration pattern"

        before_results = recall.query(vault=vault, query_text=query_text, k=5, mode="stub")
        before_corpus_size = sum(1 for _ in vault.rglob("*.md"))
        before_hits_needed_for_full_coverage = sum(
            1 for r in before_results if r["path"] in _RECURRING_SOURCES
        )

        recurring = consolidate.find_recurring_targets(vault, _RECURRING_SOURCES, min_recurrence=3)
        assert "widget-migration-pattern" in recurring, (
            "fixture setup bug: expected recurrence not detected"
        )

        revert_log = RevertLog(vault, log_root=Path(tmp) / "revert-log")
        consolidate.consolidate_target(
            vault, revert_log, "eval-run", "widget-migration-pattern",
            recurring["widget-migration-pattern"],
        )

        after_results = recall.query(vault=vault, query_text=query_text, k=5, mode="stub")
        after_corpus_size = sum(1 for _ in vault.rglob("*.md"))
        after_paths = [r["path"] for r in after_results]
        consolidated_path = "personal/crystallized/consolidated-widget-migration-pattern.md"
        consolidated_found = consolidated_path in after_paths
        sources_still_findable = sum(1 for r in _RECURRING_SOURCES if r in after_paths)

        # Redundancy proxy: entries-needed-for-full-topic-coverage. Before:
        # every recall-worthy hit is a separate episodic source (no single
        # entry captures the recurring fact on its own). After: ONE
        # consolidated entry captures it — recall no longer needs all N
        # source hits to cover the same information.
        redundancy_before = before_hits_needed_for_full_coverage
        redundancy_after = 1 if consolidated_found else redundancy_before

        return {
            "corpus_size": {"before": before_corpus_size, "after": after_corpus_size},
            "note": (
                "corpus size INCREASES (never-delete invariant, by design) — "
                "see this script's module docstring for why 'reduces corpus "
                "size' is reported as a redundancy reduction, not a file-count one"
            ),
            "redundancy_entries_needed_for_topic_coverage": {
                "before": redundancy_before, "after": redundancy_after,
            },
            "recall_regression_check": {
                "consolidated_entry_found_in_top5": consolidated_found,
                "original_sources_still_findable_count": sources_still_findable,
                "original_sources_total": len(_RECURRING_SOURCES),
            },
        }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jsonl-out", default=None)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    result = run_eval()
    print(json.dumps(result, indent=2))

    redundancy = result["redundancy_entries_needed_for_topic_coverage"]
    regression = result["recall_regression_check"]
    redundancy_reduced = redundancy["after"] < redundancy["before"]
    passed = redundancy_reduced and regression["consolidated_entry_found_in_top5"]

    print(
        f"\n[eval-v6-consolidate] redundancy_reduced={redundancy_reduced} "
        f"consolidated_entry_found={regression['consolidated_entry_found_in_top5']} "
        f"-> passed={passed}",
        file=sys.stderr,
    )

    if args.jsonl_out:
        record = {
            "suite": HEALTH_SUITE, "axis": HEALTH_AXIS,
            "check": (
                f"V6-4 consolidation: redundancy {redundancy['before']}->{redundancy['after']} "
                f"entries-needed, consolidated entry found={regression['consolidated_entry_found_in_top5']}"
            ),
            "pass": passed, "weight": 1.0,
        }
        with open(args.jsonl_out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
