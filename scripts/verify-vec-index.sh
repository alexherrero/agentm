#!/usr/bin/env bash
# verify-vec-index.sh — the vector drain pipeline end-to-end + the freshness
# invariant (R1.4 / agentmExperience#0).
#
# Drives the REAL memory-skill CLIs against a `mktemp` scratch vault: save N
# entries (auto-enqueues via save.py's vec_index.enqueue call) → assert the
# queue grows by N → drain in deterministic `--mode stub` (no network/model,
# CI fast-tier safe) → assert entry_meta holds N rows with indexed_at > 0 →
# a keyword query surfaces a seeded entry → `full-sync` reports the freshness
# ratio at 100% (every seeded entry up to date, nothing drifted or unindexed).
#
# VERIFY_VEC_INDEX_FAULT=1 deletes the scratch vault mid-queue and asserts
# `drain` exits non-zero rather than silently reporting {"processed": 0, ...}
# — a stale-looking snapshot indistinguishable from "queue was already empty".
# This gate was RED until the drain-command existence check landed in this
# same task (vec_index.py's `drain` branch: `if not vault.is_dir(): ... return 1`).
#
# ABLATE_VECTORS=1 (PLAN-r3-uplift-scoring task 1, R3.1a) — the mechanical-
# uplift baseline off-state: same N-entry fixture corpus as the on-state, but
# `drain` is deliberately never called, so recall.py's vec search finds no
# index to query and falls back to keyword-only (grep) search. Asserts the
# keyword-only path still surfaces a seeded entry — proving the floor the
# vector index sits above, not a red/broken state. Additive to
# VERIFY_VEC_INDEX_FAULT; the two are never combined in one run.
#
# Hermetic: `--mode stub` never calls a real embedding API. The vec-index
# backend (sqlite-vec) needs a Python whose sqlite3 supports
# enable_load_extension; when unavailable this script's index-row assertions
# degrade to SKIPPED (never silently dropped), matching verify-memory-roundtrip.sh's
# convention — the enqueue/drain-exit-code assertions still run unconditionally.
#
# Note: the vec-index itself lives DEVICE-LOCAL (~/.agentm/memory/_meta/<hash>/),
# keyed by a hash of the vault's resolved path — not inside the vault, so it is
# NOT covered by this script's vault cleanup. This matches the existing
# convention in verify-memory-roundtrip.sh (same device-local index root);
# accumulated scratch-vault index dirs there are pre-existing, accepted debris,
# not something this script introduces.
#
# Usage:   bash scripts/verify-vec-index.sh
#          VERIFY_VEC_INDEX_FAULT=1 bash scripts/verify-vec-index.sh
#          ABLATE_VECTORS=1 bash scripts/verify-vec-index.sh
# Exit:    0 iff every (non-skipped) check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-vec-index: $PY not found" >&2; exit 2; }

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-vec-index"
HEALTH_AXIS="memory freshness+experience"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0; SKIP=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }
skip() { RESULTS+=("  SKIP  $1"$'\n'"          ↳ $2"); SKIP=$((SKIP+1)); emit_jsonl_check "$1" null; }

assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}

FAULT="${VERIFY_VEC_INDEX_FAULT:-}"
ABLATE="${ABLATE_VECTORS:-}"

V="$(mktemp -d)"
cleanup() { rm -rf "$V" 2>/dev/null || true; }
trap cleanup EXIT
export MEMORY_VAULT_PATH="$V"
echo "verify-vec-index: scratch vault = $V"

mem() { "$PY" "$S/$1" "${@:2}"; }

N=3

if [ "$FAULT" = "1" ]; then
  # ── fault: vault vanishes mid-queue → drain must fail loud ──────────────
  ENTRY_BODY="the deploy runbook staging gate lives at ops/deploy.md"
  printf '%s\n' "$ENTRY_BODY" | mem save.py reference deploy-runbook --tags "ops" --body-file - >/dev/null 2>&1
  rm -rf "$V"
  DRAIN_OUT="$(mem vec_index.py drain --mode stub 2>&1)"; RC=$?
  assert_equals   "fault: drain exits non-zero when the vault has vanished mid-queue" \
    "$([ "$RC" -ne 0 ] && echo yes || echo no)" "yes"
  assert_contains "fault: drain's error names the missing vault path" "$DRAIN_OUT" "does not exist"
elif [ "$ABLATE" = "1" ]; then
  # ── ablate: re-run the same A/B/C battery as the on-state, but drain is
  # deliberately never called — B fails (the same check name as on-state)
  # instead of skipping, so score_axis sees a genuine, backend-independent
  # degradation rather than an equal on/off score. C still passes via
  # recall.py's keyword-only (grep) fallback — vector index bypassed. ────
  for i in $(seq 1 "$N"); do
    printf 'deployment runbook staging gate entry number %d\n' "$i" \
      | mem save.py reference "deploy-runbook-$i" --tags "ops,deploy" --body-file - >/dev/null
  done
  QUEUE_FILE="$V/_meta/embedding-queue.jsonl"
  QUEUE_LINES="$(wc -l < "$QUEUE_FILE" 2>/dev/null | tr -d ' ')"
  assert_equals "A. save x$N: embedding queue grew by $N" "${QUEUE_LINES:-0}" "$N"
  # Shell-level PASS (this is the correctly-expected ablated behavior, same
  # convention as VERIFY_VEC_INDEX_FAULT's own assertions); the JSONL record
  # is emitted directly with pass=0, same check name as the on-state's own
  # "B" check — it represents whether the DRAIN capability functioned,
  # which under ablation it deliberately did not.
  RESULTS+=("  PASS  ablate: B. drain --mode stub was correctly never invoked (vector index bypassed)")
  PASS=$((PASS+1))
  emit_jsonl_check "B. drain --mode stub: exits 0" 0
  QUERY_OUT="$(mem recall.py query "deployment runbook staging gate" -k 5 --mode stub 2>/dev/null)"
  assert_contains "C. query: a seeded entry is surfaced" "$QUERY_OUT" "deploy-runbook-"
else
  # ── A. save N entries → queue grows by N ────────────────────────────────
  for i in $(seq 1 "$N"); do
    printf 'deployment runbook staging gate entry number %d\n' "$i" \
      | mem save.py reference "deploy-runbook-$i" --tags "ops,deploy" --body-file - >/dev/null
  done
  QUEUE_FILE="$V/_meta/embedding-queue.jsonl"
  QUEUE_LINES="$(wc -l < "$QUEUE_FILE" 2>/dev/null | tr -d ' ')"
  assert_equals "A. save x$N: embedding queue grew by $N" "${QUEUE_LINES:-0}" "$N"

  # ── B. drain (stub mode) → entry_meta holds N rows, indexed_at > 0 ──────
  DRAIN_OUT="$(mem vec_index.py drain --mode stub 2>&1)"; RC=$?
  assert_equals "B. drain --mode stub: exits 0" "$RC" "0"
  META="$("$PY" -c "
import sys, pathlib; sys.path.insert(0, '$S')
import vec_index
conn = vec_index._open_index(pathlib.Path('$V'))
if conn is None:
    print('SKIP')
else:
    rows = conn.execute('SELECT COUNT(*), MIN(indexed_at) FROM entry_meta').fetchone()
    conn.close()
    print(f'{rows[0]},{rows[1]}')
" 2>/dev/null)"
  # Backend availability gates B/D/E's index-content assertions (SKIPPED, never
  # silently dropped) — mirrors verify-memory-roundtrip.sh: sqlite-vec needs a
  # Python whose sqlite3 supports enable_load_extension (disabled on Apple's
  # system Python; CI installs sqlite-vec to exercise this for real). A/C don't
  # depend on it — the queue write is backend-independent and recall.py's
  # query() falls back to keyword search when vec search is unavailable.
  VEC_BACKEND_UP=1
  if [ "$META" = "SKIP" ]; then
    VEC_BACKEND_UP=0
    skip "B. entry_meta: $N rows with indexed_at > 0" "sqlite-vec backend unavailable on this Python"
  else
    META_COUNT="${META%%,*}"; META_MIN_INDEXED="${META##*,}"
    assert_equals "B. entry_meta holds $N rows" "$META_COUNT" "$N"
    if [ "${META_MIN_INDEXED:-0}" -gt 0 ] 2>/dev/null; then
      pass "B. entry_meta: every row has indexed_at > 0"
    else
      fail "B. entry_meta: every row has indexed_at > 0" "min(indexed_at)=$META_MIN_INDEXED"
    fi
  fi

  # ── C. semantically-near query surfaces a seeded entry ──────────────────
  QUERY_OUT="$(mem recall.py query "deployment runbook staging gate" -k 5 --mode stub 2>/dev/null)"
  assert_contains "C. query: a seeded entry is surfaced" "$QUERY_OUT" "deploy-runbook-"

  if [ "$VEC_BACKEND_UP" -eq 1 ]; then
    # ── D. freshness invariant: full-sync reports 100% up to date ─────────
    FS="$(mem vec_index.py full-sync 2>/dev/null)"
    FS_PARSED="$("$PY" -c "
import json,sys
try:
    d=json.loads('''$FS''')
    print(f\"{d.get('up_to_date_count',0)},{d.get('drifted_count',0)},{d.get('not_indexed_count',0)}\")
except Exception:
    print('ERR,ERR,ERR')
" 2>/dev/null)"
    UP="${FS_PARSED%%,*}"; REST="${FS_PARSED#*,}"; DRIFTED="${REST%%,*}"; NOTIDX="${REST##*,}"
    if [ "$UP" = "ERR" ]; then
      fail "D. freshness: full-sync output parses" "got: $FS"
    else
      assert_equals "D. freshness: all $N seeded entries are up to date" "$UP" "$N"
      assert_equals "D. freshness: nothing drifted" "$DRIFTED" "0"
      assert_equals "D. freshness: nothing unindexed" "$NOTIDX" "0"
    fi

    # ── E. vault_lint.py --check-freshness reports ratio 1.0 (doctor wiring) ─
    LINT_OUT="$(mem vault_lint.py --vault "$V" --check-freshness --format json 2>/dev/null)"
    assert_contains "E. vault_lint --check-freshness: ratio 1.0 with a fully-drained queue" "$LINT_OUT" '"ratio": 1.0'
  else
    skip "D. freshness invariant (full-sync 100% up to date)" "sqlite-vec backend unavailable on this Python"
    skip "E. vault_lint --check-freshness ratio 1.0" "sqlite-vec backend unavailable on this Python"
  fi
fi

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-vec-index: $PASS passed, $FAIL failed, $SKIP skipped"
[ "$FAIL" -eq 0 ]
