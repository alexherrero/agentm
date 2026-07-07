#!/usr/bin/env bash
# verify-dreaming.sh — end-to-end regression guard for the dreaming pipeline
# (AG Wave E, PLAN-wave-e-dreaming) and the health-scorecard's live signal
# for the `memory freshness+experience` axis's dreaming row.
#
# Until this script existed, dreaming's only scorecard presence was a static
# "designed, not built" row in scripts/health/dark-checks.jsonl — a
# placeholder that never scores, only flags a known gap. This script is the
# real, scored replacement: it drives the actual CLIs (dream.py,
# dream_confirm.py, revert_log.py, the templates/jobs/dream.yaml manifest)
# against a scratch vault and asserts genuine end-to-end behavior, the same
# way verify-hook-resolution.sh / verify-reflection.sh do for their own
# axes. Unit-level coverage of every stage's logic already lives in
# scripts/test_dream.py / test_dream_confirm.py / test_revert_log.py /
# test_dream_job.py — this script proves the WIRING, not every branch.
#
# Checks:
#   A. a manual `/dream` run against a seeded fixture corpus (a near-
#      duplicate pair) stages a dedup proposal with a revert pointer
#   B. no source file is mutated by the run itself (byte-identical corpus)
#   C. the derived-insight write is status: candidate
#   D. confirming the staged proposal applies it through revert_log, and
#      reverting via the SAME RevertLog undoes it (round-trip proof that
#      apply routes through the journal, not a direct write)
#   E. an expired proposal's confirm() raises and never applies (no silent
#      apply on timeout)
#   F. the shipped job manifest (templates/jobs/dream.yaml) parses and
#      stays dry_run: true
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

# ── scratch vault + a sibling scratch dir for revert-log state and byte-exact
#    pre-image backups (isolated; auto-removed) ─────────────────────────────
SCRATCH="$(mktemp -d)"
SV="$SCRATCH/vault"
BACKUPS="$SCRATCH/backups"
mkdir -p "$SV" "$BACKUPS"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
echo "verify-dreaming: scratch vault=$SV"

printf -- '---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n' > "$SV/a.md"
printf -- '---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n' > "$SV/b.md"
PRE_A="$(cat "$SV/a.md")"
PRE_B="$(cat "$SV/b.md")"
# Byte-exact backups for the Python round-trip check below — command
# substitution ($(...)) strips trailing newlines, so PRE_A/PRE_B above are
# fine for grep-style substring assertions but NOT safe to re-embed into a
# Python string literal for an exact-equality check (D.).
cp "$SV/a.md" "$BACKUPS/a-orig.md"

# ── A/B/C. a manual /dream run stages a dedup proposal, mutates nothing,
#          and writes a status: candidate insight ─────────────────────────
DREAM_OUT="$("$PY" "$S/dream.py" --vault-path "$SV" --run-id verify-run 2>&1)"
DIGEST="$SV/_dream-staging/verify-run/digest.md"

assert_eq "A. dream run exits describing the dedup proposal" \
  "$(printf '%s' "$DREAM_OUT" | grep -c 'proposal(s)')" "1"
assert_contains "A. digest carries a revert pointer" "$(cat "$DIGEST" 2>/dev/null)" "revert pointer"

POST_A="$(cat "$SV/a.md")"
POST_B="$(cat "$SV/b.md")"
assert_eq "B. source entry a.md untouched by the run itself" "$POST_A" "$PRE_A"
assert_eq "B. source entry b.md untouched by the run itself" "$POST_B" "$PRE_B"

INSIGHT="$SV/_dream/insights/verify-run.md"
if [ -f "$INSIGHT" ]; then
  assert_contains "C. insight candidate is status: candidate" "$(cat "$INSIGHT")" "status: candidate"
else
  fail "C. insight candidate is status: candidate" "no insight file at $INSIGHT"
fi

# ── D. confirm applies through revert_log; the SAME RevertLog can undo it ──
CONFIRM_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
from revert_log import RevertLog
import dream_confirm as dc

vault = '$SV'
pre_a = open('$BACKUPS/a-orig.md', 'rb').read()
rl = RevertLog(vault, log_root='$SCRATCH/rl-log', lock_root='$SCRATCH/rl-lock')
entry_id = dc.confirm(vault, 'verify-run', 1, rl)
mutated = open('$SV/a.md', 'rb').read()
rl.revert('verify-run', entry_id=entry_id)
reverted = open('$SV/a.md', 'rb').read()
print('MUTATED_DIFFERS=' + str(mutated != pre_a))
print('REVERTED_MATCHES=' + str(reverted == pre_a))
" 2>&1)"
assert_contains "D. confirm() actually applied the mutation" "$CONFIRM_OUT" "MUTATED_DIFFERS=True"
assert_contains "D. RevertLog.revert() undid the confirmed apply (routed through the journal)" "$CONFIRM_OUT" "REVERTED_MATCHES=True"

# ── E. an expired proposal's confirm() raises and never applies ────────────
printf -- '---\nkind: fix\n---\nCompletely unrelated content, entry one.\n' > "$SV/e1.md"
printf -- '---\nkind: fix\n---\nCompletely unrelated content, entry one!\n' > "$SV/e2.md"
PRE_E1="$(cat "$SV/e1.md")"
"$PY" "$S/dream.py" --vault-path "$SV" --run-id verify-expire >/dev/null 2>&1

EXPIRE_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
from revert_log import RevertLog
import dream_confirm as dc

vault = '$SV'
rl = RevertLog(vault, log_root='$SCRATCH/rl-log2', lock_root='$SCRATCH/rl-lock2')
staged_at = __import__('json').load(open(vault + '/_dream-staging/verify-expire/proposals.json'))['staged_at']
far_future = staged_at + (dc.DEFAULT_TTL_DAYS + 1) * 86400
try:
    dc.confirm(vault, 'verify-expire', 1, rl, now=far_future)
    print('RAISED=False')
except dc.ExpiredProposalError:
    print('RAISED=True')
" 2>&1)"
assert_contains "E. confirm() on an expired proposal raises ExpiredProposalError" "$EXPIRE_OUT" "RAISED=True"

POST_E1="$(cat "$SV/e1.md")"
assert_eq "E. expired proposal's source entry stays untouched (no silent apply on timeout)" "$POST_E1" "$PRE_E1"

# ── F. the shipped job manifest parses and stays dry_run: true ─────────────
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
" 2>&1)"
assert_contains "F. templates/jobs/dream.yaml parses per the runner's manifest schema" "$MANIFEST_OUT" "NAME=dream"
assert_contains "F. shipped manifest stays dry_run: true (no live promotion)" "$MANIFEST_OUT" "DRY_RUN=True"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-dreaming: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
