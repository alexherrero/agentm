#!/usr/bin/env bash
# verify-opinion-supplements.sh — end-to-end regression guard for the
# accumulate loop's Stages 2-3 (wiki/designs/agentm-experience-and-
# dreaming.md's accumulate-loop section, ten locked calls, 2026-07-25) and
# the health-scorecard's live signal for the `memory freshness+experience`
# axis's opinion-supplement row.
#
# Unit-level coverage of every piece already lives in
# scripts/test_opinion_supplement.py (the recurrence gate, contradiction
# check, composition) and scripts/test_dream.py's OpinionSupplementStage-
# Tests (the dream.py wiring) — this script proves the WIRING across the
# CLIs end to end the way verify-dreaming.sh does for its own axis.
#
# Checks:
#   A. two Stage-1-shaped lane entries with 2 DISTINCT sessions stage a
#      stage="opinion_promote" proposal — run_dream() is propose-only, so
#      the served supplement does not exist yet
#   B. opinion_promote is NOT in dream_confirm.AUTO_APPLY_STAGES, and
#      run_dream_and_auto_apply() never serves it (stays pending)
#   C. confirming the proposal applies it through revert_log — the served
#      file appears, and opinion_resolver.opinion_resolve() reads it back
#      as "served" (base + supplement both present)
#   D. reverting via the SAME RevertLog undoes it (served file gone again)
#   E. a group that directly reverses its coded base on a shared anchor
#      never gets served, and records a proposed base change in
#      _meta/opinion-base-proposals.json
#   F. the health pointer file reflects lane depth/promoted counts, and
#      console.py's section renders it without raising
#   G. kind_registry recognizes kind: opinion-supplement (locked call 3)
#
# Usage:   bash scripts/verify-opinion-supplements.sh
# Exit:    0 iff every check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"
CONSOLE="$REPO/harness/skills/console/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-opinion-supplements: $PY not found" >&2; exit 2; }

HEALTH_SUITE="verify-opinion-supplements"
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
assert_not_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1" "did NOT expect substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-200)"
  else pass "$1"; fi
}
assert_eq() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '$3'  got '$2'"; fi
}

# ── scratch vault (isolated; auto-removed) ──────────────────────────────────
SCRATCH="$(mktemp -d)"
# Hermetic engine state (filing-v2 part 2a): machine state lives at
# $AGENTM_STATE_DIR now, so the scratch run gets its own.
export AGENTM_STATE_DIR="$SCRATCH/engine-state"
mkdir -p "$AGENTM_STATE_DIR"

SV="$SCRATCH/vault"
mkdir -p "$SV"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
echo "verify-opinion-supplements: scratch vault=$SV"

LANE="$SV/memory/_opinions/good"
mkdir -p "$LANE"
printf -- '---\nkind: opinion-supplement\nstatus: proposed\ncreated: 2026-01-01T00:00:00+00:00\nslug: a1\nopinion: good\nsessions: [proj/s1]\n---\n\n## Always run the linter before committing\n\nRun the linter first, always.\n' > "$LANE/a1.md"
printf -- '---\nkind: opinion-supplement\nstatus: proposed\ncreated: 2026-01-02T00:00:00+00:00\nslug: a2\nopinion: good\nsessions: [proj/s2]\n---\n\n## Always run the linter before committing\n\nRun the linter first, always!\n' > "$LANE/a2.md"

# ── A. run_dream() proposes, never applies ──────────────────────────────────
DREAM_OUT="$("$PY" "$S/dream.py" --vault-path "$SV" --run-id verify-run 2>&1)"
SERVED="$SV/memory/_opinions/good.md"

if [ -f "$SERVED" ]; then
  fail "A. run_dream() is propose-only (no served file yet)" "served file already exists: $SERVED"
else
  pass "A. run_dream() is propose-only (no served file yet)"
fi
assert_contains "A. dream run's own output mentions a proposal" "$DREAM_OUT" "proposal(s)"

MANIFEST="$AGENTM_STATE_DIR/dream-runs/verify-run/proposals.json"
OP_INDEX_OUT="$("$PY" -c "
import json
data = json.load(open('$MANIFEST'))
idx = [p['index'] for p in data['proposals'] if p['stage'] == 'opinion_promote']
print('COUNT=' + str(len(idx)))
print('INDEX=' + (str(idx[0]) if idx else ''))
")"
assert_contains "A. exactly one opinion_promote proposal staged" "$OP_INDEX_OUT" "COUNT=1"
OP_INDEX="$(printf '%s' "$OP_INDEX_OUT" | grep '^INDEX=' | cut -d= -f2)"

# ── B. opinion_promote is confirm-gated, never auto-applied ────────────────
AUTO_APPLY_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
import dream, dream_confirm

vault = '$SV'
print('IN_AUTO_APPLY_STAGES=' + str('opinion_promote' in dream_confirm.AUTO_APPLY_STAGES))
digest, batch = dream.run_dream_and_auto_apply(
    vault, run_id='verify-auto-apply',
    log_root='$SCRATCH/rl-log-auto', lock_root='$SCRATCH/rl-lock-auto',
)
pending = [p for p in digest.proposals if p.stage == 'opinion_promote']
print('STILL_PENDING=' + str(len(pending)))
print('STAGE_IN_BATCH=' + str('opinion_promote' in batch.stages))
" 2>&1)"
assert_contains "B. opinion_promote is not in AUTO_APPLY_STAGES" "$AUTO_APPLY_OUT" "IN_AUTO_APPLY_STAGES=False"
assert_contains "B. the proposal survives run_dream_and_auto_apply(), still pending" "$AUTO_APPLY_OUT" "STILL_PENDING=1"
assert_contains "B. opinion_promote never appears in the auto-applied batch" "$AUTO_APPLY_OUT" "STAGE_IN_BATCH=False"

if [ -f "$SERVED" ]; then
  fail "B. run_dream_and_auto_apply() never serves an opinion_promote proposal" "served file exists after auto-apply wrapper"
else
  pass "B. run_dream_and_auto_apply() never serves an opinion_promote proposal"
fi

# ── C. confirm() applies through revert_log; the resolver reads it back ────
CONFIRM_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
sys.path.insert(0, '$REPO/scripts')
from revert_log import RevertLog
import dream_confirm as dc
import opinion_resolver

vault = '$SV'
rl = RevertLog(vault, log_root='$SCRATCH/rl-log', lock_root='$SCRATCH/rl-lock')
entry_id = dc.confirm(vault, 'verify-run', $OP_INDEX, rl)
res = opinion_resolver.opinion_resolve('good', supplement_dir=vault + '/memory/_opinions')
print('REASON=' + res['reason'])
print('HAS_BASE=' + str(res['base'] is not None))
print('HAS_SUPPLEMENT=' + str(res['supplement'] is not None and 'linter' in res['supplement'].lower()))
print('ENTRY_ID=' + entry_id)
" 2>&1)"
assert_contains "C. confirm() applied the mutation" "$CONFIRM_OUT" "ENTRY_ID="
assert_contains "C. opinion_resolve() reports served" "$CONFIRM_OUT" "REASON=served"
assert_contains "C. the coded base is still present alongside the supplement" "$CONFIRM_OUT" "HAS_BASE=True"
assert_contains "C. the served supplement carries the promoted lesson's text" "$CONFIRM_OUT" "HAS_SUPPLEMENT=True"

if [ -f "$SERVED" ]; then
  pass "C. the served file exists on disk after confirm()"
else
  fail "C. the served file exists on disk after confirm()" "no file at $SERVED"
fi

# ── D. revert() undoes it through the SAME RevertLog ────────────────────────
ENTRY_ID="$(printf '%s' "$CONFIRM_OUT" | grep '^ENTRY_ID=' | cut -d= -f2)"
"$PY" -c "
import sys
sys.path.insert(0, '$S')
from revert_log import RevertLog
rl = RevertLog('$SV', log_root='$SCRATCH/rl-log', lock_root='$SCRATCH/rl-lock')
rl.revert('verify-run', entry_id='$ENTRY_ID')
"
if [ -f "$SERVED" ]; then
  fail "D. revert() removes the served file (it was newly created by confirm)" "still exists at $SERVED"
else
  pass "D. revert() removes the served file (it was newly created by confirm)"
fi

# ── E. a direct base contradiction never gets served, and is recorded ──────
# The real opinions/*.md prose is measured, non-imperative text with no
# always/must/never/don't markers to reverse today — this check proves the
# GUARD's wiring (a suspected contradiction parks instead of promoting, and
# is recorded) with a scratch coded base, monkeypatching
# opinion_supplement._repo_root() rather than touching a real opinions/
# file, exactly the seam opinion_resolver.py's own docstring names for
# this: whoever resolves the base decides where it lives.
SV2="$SCRATCH/vault2"
CLANE="$SV2/memory/_opinions/recoverable"
mkdir -p "$CLANE"
printf -- '---\nkind: opinion-supplement\nstatus: proposed\ncreated: 2026-01-01T00:00:00+00:00\nslug: c1\nopinion: recoverable\nsessions: [proj/s1]\n---\n\n## Force-push rule\n\nNever confirm before a force-push to a shared branch.\n' > "$CLANE/c1.md"
printf -- '---\nkind: opinion-supplement\nstatus: proposed\ncreated: 2026-01-02T00:00:00+00:00\nslug: c2\nopinion: recoverable\nsessions: [proj/s2]\n---\n\n## Force-push rule\n\nNever confirm before a force-push to a shared branch!\n' > "$CLANE/c2.md"

CONTRADICTION_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
from pathlib import Path
import dream, opinion_supplement

# This phase gets its own engine state: pre-2a the per-vault _meta kept
# $SV and $SV2's health pointers apart; the state dir does that now.
__import__('os').environ['AGENTM_STATE_DIR'] = '$SCRATCH/engine-state-sv2'
fake_root = Path('$SCRATCH/fake-repo')
(fake_root / 'opinions').mkdir(parents=True)
(fake_root / 'opinions' / 'recoverable.md').write_text(
    '---\nname: recoverable\nkind: opinion\n---\nYou must always confirm before a force-push to a shared branch.\n',
    encoding='utf-8',
)
opinion_supplement._repo_root = lambda root=None: (Path(root) if root is not None else fake_root)

digest = dream.run_dream('$SV2', run_id='verify-contradiction')
served = Path('$SV2') / 'personal' / '_opinions' / 'recoverable.md'
print('SERVED_EXISTS=' + str(served.is_file()))

import json
base_proposals = json.loads((Path('$SCRATCH/engine-state-sv2') / 'opinion-base-proposals.json').read_text())
hits = [p for p in base_proposals if p.get('opinion') == 'recoverable']
print('BASE_PROPOSAL_COUNT=' + str(len(hits)))
" 2>&1)"
assert_contains "E. a base-contradicting group is never served" "$CONTRADICTION_OUT" "SERVED_EXISTS=False"
assert_contains "E. the contradiction is recorded in the engine-state opinion-base-proposals.json" "$CONTRADICTION_OUT" "BASE_PROPOSAL_COUNT=1"

# ── F. the health pointer + console section render without raising ─────────
HEALTH_OUT="$("$PY" -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '$CONSOLE')
import console

vault = Path('$SV')
health = json.loads((Path(__import__('os').environ['AGENTM_STATE_DIR']) / 'opinion-supplement-health-latest.json').read_text())
print('GOOD_LANE_DEPTH=' + str(health['opinions'].get('good', {}).get('lane_depth', -1)))
section = console.section_opinion_supplements(vault)
print('SECTION_HAS_GOOD=' + str('good:' in section))
" 2>&1)"
assert_contains "F. health pointer tracks the 'good' lane (both entries back to proposed after D's revert)" "$HEALTH_OUT" "GOOD_LANE_DEPTH=2"
assert_contains "F. console.section_opinion_supplements() renders the 'good' opinion" "$HEALTH_OUT" "SECTION_HAS_GOOD=True"

# ── G. kind_registry recognizes kind: opinion-supplement (locked call 3) ───
KIND_OUT="$("$PY" -c "
import sys
sys.path.insert(0, '$S')
import kind_registry
print('KNOWN=' + str(kind_registry.is_known('opinion-supplement')))
")"
assert_contains "G. kind_registry recognizes kind: opinion-supplement" "$KIND_OUT" "KNOWN=True"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-opinion-supplements: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
