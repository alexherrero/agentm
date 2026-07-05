#!/usr/bin/env bash
# run-ablation-baseline.sh — for each of the four R3.1a subsystems (vectors,
# reflection, gates, evidence-tracker), run its verify script twice — once
# on, once with its ABLATE_*=1 off-switch — score each run's check records
# through health_score.py's own axis-scoring path, and emit one ablation
# record per subsystem: {"subsystem", "axis", "score_on", "score_off",
# "uplift"} where uplift = score_on - score_off (PLAN-r3-uplift-scoring
# task 2 / R3.1b).
#
# Deterministic, fixture-driven, no live model calls — belongs in the fast,
# every-run tier (wired into check-all.sh as its own gate line). The uplift
# MAGNITUDE is advisory reading, never a pass/fail gate; this script's own
# exit code is 0 unless a subsystem's on/off pair can't be scored at all
# (a setup failure, not a low uplift number).
#
# evidence-tracker lives in the crickets sibling repo (no presence in
# agentm) — its record is derived directly from evidence_tracker.cli_check()
# against the same bad-flip fixture the crickets self-test already carries
# (test_edit_blocks_without_evidence / test_ablate_evidence_tracker_flip_
# sails_through_undetected), not from the self-test's own exit code (the
# self-test always passes both permanent regression tests regardless of the
# ambient environment — the env var is scoped inside each test, not read
# from this script's process environment). Graceful-skip (record omitted,
# noted on stderr) when no crickets checkout is found as a sibling
# directory — this script never hard-fails on an absent sibling repo.
#
# Usage:   bash scripts/health/run-ablation-baseline.sh [--jsonl-out <path>]
# Exit:    0 unless a subsystem's on/off pair can't be scored at all.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$SCRIPTS_DIR/.." && pwd)"
PY="${PYTHON:-python3}"

OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jsonl-out) OUT="${2:?--jsonl-out needs a value}"; shift 2 ;;
    --jsonl-out=*) OUT="${1#--jsonl-out=}"; shift ;;
    *) echo "run-ablation-baseline: unknown arg: $1" >&2; exit 2 ;;
  esac
done

FAIL=0

score_axis() {  # score_axis <jsonl-file> <axis>
  "$PY" -c "
import sys
sys.path.insert(0, '$HERE')
import health_score as hs
records = [r for r in hs.read_records('$1') if r.get('axis') == '$2']
score, live, dark = hs.score_axis(records)
print(score if live else 'NONE')
"
}

emit_record() {  # emit_record <subsystem> <axis> <score_on> <score_off>
  local subsystem="$1" axis="$2" on="$3" off="$4"
  if [ "$on" = "NONE" ] || [ "$off" = "NONE" ]; then
    echo "run-ablation-baseline: $subsystem: no scorable records for axis '$axis' — skipping" >&2
    FAIL=1
    return
  fi
  "$PY" -c "
import json
on, off = $on, $off
print(json.dumps({'subsystem': '$subsystem', 'axis': '$axis', 'score_on': on, 'score_off': off, 'uplift': round(on - off, 2)}))
" | tee ${OUT:+-a "$OUT"}
}

echo "run-ablation-baseline: vectors — on/off pair" >&2
ON_JSONL="$(mktemp)"; OFF_JSONL="$(mktemp)"
bash "$SCRIPTS_DIR/verify-vec-index.sh" --jsonl-out "$ON_JSONL" >&2 || true
ABLATE_VECTORS=1 bash "$SCRIPTS_DIR/verify-vec-index.sh" --jsonl-out "$OFF_JSONL" >&2 || true
emit_record "vectors" "memory freshness+experience" \
  "$(score_axis "$ON_JSONL" "memory freshness+experience")" \
  "$(score_axis "$OFF_JSONL" "memory freshness+experience")"
rm -f "$ON_JSONL" "$OFF_JSONL"

echo "run-ablation-baseline: reflection — on/off pair" >&2
ON_JSONL="$(mktemp)"; OFF_JSONL="$(mktemp)"
bash "$SCRIPTS_DIR/verify-reflection.sh" --jsonl-out "$ON_JSONL" >&2 || true
ABLATE_REFLECTION=1 bash "$SCRIPTS_DIR/verify-reflection.sh" --jsonl-out "$OFF_JSONL" >&2 || true
emit_record "reflection" "memory persist+recall" \
  "$(score_axis "$ON_JSONL" "memory persist+recall")" \
  "$(score_axis "$OFF_JSONL" "memory persist+recall")"
rm -f "$ON_JSONL" "$OFF_JSONL"

echo "run-ablation-baseline: gates — on/off pair" >&2
ON_JSONL="$(mktemp)"; OFF_JSONL="$(mktemp)"
bash "$SCRIPTS_DIR/health/validate-audit-coverage.sh" --jsonl-out "$ON_JSONL" >&2 || true
ABLATE_GATES=1 bash "$SCRIPTS_DIR/health/validate-audit-coverage.sh" --jsonl-out "$OFF_JSONL" >&2 || true
emit_record "gates" "capability function" \
  "$(score_axis "$ON_JSONL" "capability function")" \
  "$(score_axis "$OFF_JSONL" "capability function")"
rm -f "$ON_JSONL" "$OFF_JSONL"

CRICKETS="$(cd "$REPO/.." 2>/dev/null && pwd)/crickets"
ET="$CRICKETS/src/code-review/hooks/evidence-tracker/evidence_tracker.py"
if [ -f "$ET" ]; then
  echo "run-ablation-baseline: evidence-tracker — on/off pair (crickets sibling checkout found)" >&2
  ON_JSONL="$(mktemp)"; OFF_JSONL="$(mktemp)"
  "$PY" -c "
import json, sys
sys.path.insert(0, '$CRICKETS/src/code-review/hooks/evidence-tracker')
import tempfile, os
from pathlib import Path
import evidence_tracker as et

def bad_flip_fixture():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / '.harness').mkdir()
    plan = root / '.harness' / 'PLAN.md'
    plan.write_text(
        '# Plan\n\n## Tasks\n\n### 1. Task with heuristic\n'
        '- **Verification:** tests/foo.py passes\n- **Status:** [ ]\n',
        encoding='utf-8',
    )
    event = json.dumps({
        'tool_name': 'Edit',
        'tool_input': {
            'file_path': str(plan),
            'old_string': '- **Verification:** tests/foo.py passes\n- **Status:** [ ]',
            'new_string': '- **Verification:** tests/foo.py passes\n- **Status:** [x] — done',
        },
    })
    rc = et.cli_check(event, root)
    tmp.cleanup()
    return rc

rc_on = bad_flip_fixture()
with open('$ON_JSONL', 'a') as fh:
    fh.write(json.dumps({'suite': 'evidence-tracker-ablation', 'axis': 'verification honesty', 'check': 'bad-flip-blocked', 'weight': 1.0, 'pass': rc_on == 2}) + '\n')

os.environ['ABLATE_EVIDENCE_TRACKER'] = '1'
rc_off = bad_flip_fixture()
del os.environ['ABLATE_EVIDENCE_TRACKER']
with open('$OFF_JSONL', 'a') as fh:
    fh.write(json.dumps({'suite': 'evidence-tracker-ablation', 'axis': 'verification honesty', 'check': 'bad-flip-blocked', 'weight': 1.0, 'pass': rc_off == 2}) + '\n')
"
  emit_record "evidence-tracker" "verification honesty" \
    "$(score_axis "$ON_JSONL" "verification honesty")" \
    "$(score_axis "$OFF_JSONL" "verification honesty")"
  rm -f "$ON_JSONL" "$OFF_JSONL"
else
  echo "run-ablation-baseline: evidence-tracker — no crickets sibling checkout at $CRICKETS, skipping (graceful)" >&2
fi

exit $FAIL
