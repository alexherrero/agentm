#!/usr/bin/env bash
# verify-dreaming.sh — end-to-end regression guard for the dreaming pipeline
# (AG Wave E, PLAN-wave-e-dreaming) and the health-scorecard's live signal
# for the `memory freshness+experience` axis's dreaming row.
#
# Until this script existed, dreaming's only scorecard presence was a static
# "designed, not built" row in scripts/health/dark-checks.jsonl — a
# placeholder that never scores, only flags a known gap. This script is the
# real, scored replacement: it drives the actual CLI (dream.py and the
# templates/jobs/dream.yaml manifest) against a scratch vault and asserts
# genuine end-to-end behavior, the same way verify-hook-resolution.sh /
# verify-reflection.sh do for their own axes. Unit-level coverage of every
# stage's logic lives in scripts/test_dream.py / test_needs_review.py /
# test_dream_job.py — this script proves the WIRING, not every branch.
#
# Checks (narrowed in agentm-vault plan 04, when the cycle stopped applying
# anything):
#   A. a manual `/dream` run against a seeded fixture corpus (a near-
#      duplicate pair) finds it as a possible twin
#   B. no source file is mutated by the run (byte-identical corpus)
#   C. the twin reaches the needs-review map as a section of its own
#   F. the shipped job manifest (templates/jobs/dream.yaml) parses, runs
#      inside the night's window in its order, and stays dry_run: true
#
# Retired with the stages they exercised: the derived-insight write (C), the
# confirm-and-revert round trip (D) and the expired-proposal refusal (E).
#
# Usage:   bash scripts/verify-dreaming.sh
# Exit:    0 iff every check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-dreaming: $PY not found" >&2; exit 2; }

HEALTH_SUITE="verify-dreaming"
HEALTH_AXIS="memory freshness+experience"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-200)"; fi
}
assert_eq() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '$3'  got '$2'"; fi
}

# ── scratch vault (isolated; auto-removed) ─────────────────────────────────
SCRATCH="$(mktemp -d)"
# Hermetic engine state (filing-v2 part 2a): machine state lives at
# $AGENTM_STATE_DIR now, so the scratch run gets its own.
export AGENTM_STATE_DIR="$SCRATCH/engine-state"
mkdir -p "$AGENTM_STATE_DIR"

SV="$SCRATCH/vault"
mkdir -p "$SV"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
echo "verify-dreaming: scratch vault=$SV"

printf -- '---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n' > "$SV/a.md"
printf -- '---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n' > "$SV/b.md"
PRE_A="$(cat "$SV/a.md")"
PRE_B="$(cat "$SV/b.md")"

# ── A/B/C. a manual /dream run finds the twin, mutates nothing, and puts
#          the pair on the needs-review map ────────────────────────────────
DREAM_OUT="$("$PY" "$S/dream.py" --vault-path "$SV" --run-id verify-run 2>&1)"
DIGEST="$AGENTM_STATE_DIR/dream-runs/verify-run/digest.md"

assert_contains "A. dream run reports the twin" "$DREAM_OUT" "1 possible-twin"
assert_contains "A. digest lists it for you to judge" "$(cat "$DIGEST" 2>/dev/null)" \
  "dedup · possible-twin: a.md and b.md"

POST_A="$(cat "$SV/a.md")"
POST_B="$(cat "$SV/b.md")"
assert_eq "B. source entry a.md untouched by the run itself" "$POST_A" "$PRE_A"
assert_eq "B. source entry b.md untouched by the run itself" "$POST_B" "$PRE_B"

MOC="$SV/memory/mocs/needs-review.md"
assert_contains "C. the needs-review map carries a Possible twins section" \
  "$(cat "$MOC" 2>/dev/null)" "## Possible twins (1)"
assert_contains "C. the section names both notes" "$(cat "$MOC" 2>/dev/null)" "[[a]] and [[b]]"
if [ -e "$AGENTM_STATE_DIR/dream-staging" ] || [ -e "$SV/_dream" ]; then
  fail "C. nothing staged, no insight written" "found dream-staging or _dream/ after the run"
else
  pass "C. nothing staged, no insight written"
fi

# ── F. the shipped job manifest parses, sits in the night, stays dry_run ───
MANIFEST_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$REPO/scripts')
from runner import manifest
import tempfile, shutil
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
shutil.copy('$REPO/templates/jobs/dream.yaml', tmp / 'dream.yaml')
jobs = manifest.load_manifests(tmp)
job = jobs[0]
print('NAME=' + job.name)
print('DRY_RUN=' + str(job.dry_run))
print('TIER=' + job.tier)
print('WINDOW=' + str(job.window))
print('ORDER=' + str(job.order))
" 2>&1)"
assert_contains "F. templates/jobs/dream.yaml parses per the runner's manifest schema" "$MANIFEST_OUT" "NAME=dream"
assert_contains "F. shipped manifest stays dry_run: true (no live promotion)" "$MANIFEST_OUT" "DRY_RUN=True"
assert_contains "F. it starts inside the night's window" "$MANIFEST_OUT" "WINDOW=02:00-06:00"
assert_contains "F. third in the night's order" "$MANIFEST_OUT" "ORDER=3"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-dreaming: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
