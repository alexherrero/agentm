#!/usr/bin/env bash
# verify-v4.sh — kernel integration check for V4 #23 auto-orchestration
# invariants: config-seed · idle-chain · emit-gating/atomic-state.
#
# V5-5 (task 4) fracture: briefing+nudge signals → verify-orchestration-briefing.sh
# (PM-half); phase-dispatch checks → verify-phases.sh (Developer half). This file
# keeps only the kernel-owned contracts.
#
# Runs the REAL scripts against a throwaway SCRATCH vault (a mktemp dir) — it
# never reads or writes a real vault, never hits the network, never mines real
# transcripts, never dispatches a sub-agent. It asserts the deterministic
# CLI/render outputs, cleans up after itself, and prints a PASS/FAIL table.
# Complements the unit suite (`scripts/test_*.py`): those test functions in
# isolation; this tests the installed scripts wiring together via their CLIs.
#
# Usage:   bash scripts/verify-v4.sh
# Exit:    0 iff every check passes (CI / integration-test friendly).
#
# Extending: as you add a kernel signal/behavior, add a `check` that drives the
# real script against $SV and asserts the output. Keep every check scratch-isolated
# (read/write only under $SV) so the test stays hermetic and side-effect-free.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"   # memory skill scripts

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-v4: $PY not found" >&2; exit 2; }

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-v4"
HEALTH_AXIS="capability function"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

# assert_contains <desc> <haystack> <needle>
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-140)"; fi
}
# assert_equals <desc> <actual> <expected>
assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}

# ── scratch vault (isolated; auto-removed) ──────────────────────────────────
SV="$(mktemp -d)"
cleanup() { rm -rf "$SV"; }
trap cleanup EXIT

echo "verify-v4: scratch vault = $SV"

# ── A. config seed + parse ──────────────────────────────────────────────────
"$PY" "$S/auto_orchestration.py" --vault-path "$SV" seed-config >/dev/null 2>&1
[ -f "$SV/personal/auto-orchestration-config.md" ] \
  && pass "config: seed-config materializes the operator config" \
  || fail "config: seed-config materializes the operator config" "no file created"
CFG="$("$PY" "$S/auto_orchestration.py" --vault-path "$SV" show-config 2>/dev/null)"
assert_contains "config: show-config emits valid keys"          "$CFG" '"briefing_cooldown_hours"'
assert_contains "config: defaults parse (inbox_threshold=10)"   "$CFG" '"inbox_threshold": 10'
assert_contains "config: defaults parse (idle cooldown=24)"     "$CFG" '"idle_chain_cooldown_hours": 24'

# ── E. idle chain (dry-run: ordering + bounded flags) ───────────────────────
IDLE="$("$PY" "$S/orchestration_idle.py" --vault-path "$SV" --dry-run 2>/dev/null)"
assert_contains "idle: dry-run status"                          "$IDLE" '"status": "dry-run"'
assert_contains "idle: step 1 reflect-corpus"                   "$IDLE" 'reflect-corpus'
assert_contains "idle: step 2 discover-skills"                  "$IDLE" 'discover-skills'
assert_contains "idle: bounded (--max-batches)"                 "$IDLE" '"--max-batches"'
assert_contains "idle: bounded (--limit)"                       "$IDLE" '"--limit"'

# ── G-seed: minimal inbox signal so emit-gating check has content ────────────
mkdir -p "$SV/_inbox"; for i in $(seq 1 10); do echo x > "$SV/_inbox/e$i.md"; done

# ── G. emit gating (shifted-guard + cooldown) + atomic state ────────────────
# (run last — these mutate the scratch STATE file)
EMIT1="$("$PY" "$S/orchestration_briefing.py" --vault-path "$SV" 2>/dev/null)"
assert_contains "emit: first run emits the briefing block"      "$EMIT1" "MemoryVault — pending"
EMIT2="$("$PY" "$S/orchestration_briefing.py" --vault-path "$SV" 2>/dev/null)"
assert_equals  "emit: second run (cooldown/unchanged) is silent" "$EMIT2" ""
[ -f "$SV/_meta/auto-orchestration-state.json" ] \
  && pass "state: emit records the state file" \
  || fail "state: emit records the state file" "no file created"
TMPS="$(find "$SV/_meta" -maxdepth 1 -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')"
assert_equals  "state: atomic write leaves no .tmp artifact"    "$TMPS" "0"
# V5-5 [LC-2] single-writer: save_state is defined only in auto_orchestration.py;
# sibling scripts (orchestration_{phase,idle,briefing}) call ao.save_state() — they
# never define their own writer or touch the state file directly.
SW_DEFS="$(grep -l "^def save_state" "$S"/orchestration_*.py 2>/dev/null | wc -l | tr -d ' ')"
assert_equals  "state: single-writer — save_state not redefined in sibling scripts" "$SW_DEFS" "0"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-v4: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
