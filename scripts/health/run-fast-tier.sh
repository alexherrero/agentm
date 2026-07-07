#!/usr/bin/env bash
# run-fast-tier.sh — run every fast-tier verify suite with JSONL emission on,
# collect the records, and print them to stdout (R1.8 Task 2).
#
# Usage:
#   bash scripts/health/run-fast-tier.sh | python3 scripts/health/health_score.py
#
# Each verify script's own PASS/FAIL/SKIP table still prints to this script's
# stderr (unsuppressed — an operator running this directly still sees the
# familiar per-suite report); only the JSONL check records go to stdout, so
# the pipe into health_score.py sees JSONL only. A suite that fails does NOT
# abort the batch — the scorecard's job is to report health, not to gate
# (check-all.sh is the gate); every suite gets a chance to contribute records
# regardless of whether its own exit code was 0.
#
# Exit: always 0 (this script itself never fails the battery — an individual
# suite's PASS/FAIL is data for the scorecard to render, not this script's
# own outcome).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"

JSONL_TMP="$(mktemp)"
trap 'rm -f "$JSONL_TMP"' EXIT

run_suite() {  # run_suite <label> <cmd...>
  local label="$1"; shift
  echo "run-fast-tier: running $label…" >&2
  if ! "$@" --jsonl-out "$JSONL_TMP" >&2; then
    echo "run-fast-tier: $label exited non-zero (recorded in the JSONL; batch continues)" >&2
  fi
}

run_suite "verify-v4"                       bash "$SCRIPTS_DIR/verify-v4.sh"
run_suite "verify-orchestration-briefing"   bash "$SCRIPTS_DIR/verify-orchestration-briefing.sh"
run_suite "verify-hook-resolution"          bash "$SCRIPTS_DIR/verify-hook-resolution.sh"
run_suite "verify-state-routing"            bash "$SCRIPTS_DIR/verify-state-routing.sh"
run_suite "verify-vec-index"                bash "$SCRIPTS_DIR/verify-vec-index.sh"
run_suite "verify-reflection"               bash "$SCRIPTS_DIR/verify-reflection.sh"
run_suite "verify-mcp-surface"              "$PY" "$SCRIPTS_DIR/verify-mcp-surface.py"
run_suite "verify-phases"                   bash "$SCRIPTS_DIR/verify-phases.sh"
run_suite "eval-v6-graph"                    "$PY" "$HERE/eval_v6_graph.py"
run_suite "eval-v6-retrieval"                "$PY" "$HERE/eval_v6_retrieval.py"
run_suite "verify-memory-roundtrip"         bash "$SCRIPTS_DIR/verify-memory-roundtrip.sh"

cat "$JSONL_TMP"
exit 0
