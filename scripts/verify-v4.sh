#!/usr/bin/env bash
# verify-v4.sh — end-to-end integration check for the V4 #23 auto-orchestration
# push surface (briefing · idle chain · phase-dispatch · nudges · config/state).
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
# Extending: as you add a signal/behavior, add a `check` that drives the real
# script against $SV and asserts the output. Keep every check scratch-isolated
# (read/write only under $SV + the exported IDEAS_SURFACE_PATH) so the test
# stays hermetic and side-effect-free.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"   # memory skill scripts
HM="$REPO/scripts/harness_memory.py"      # harness↔memory bridge

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-v4: $PY not found" >&2; exit 2; }

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }

# assert_contains <desc> <haystack> <needle>
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-140)"; fi
}
# assert_equals <desc> <actual> <expected>
assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
# assert_absent <desc> <haystack> <needle>  (passes iff needle NOT present)
assert_absent() {
  if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1" "did not expect '$3'"; else pass "$1"; fi
}

# ── scratch vault (isolated; auto-removed) ──────────────────────────────────
SV="$(mktemp -d)"
cleanup() { rm -rf "$SV"; }
trap cleanup EXIT
# Isolate the Ideas surface (otherwise the idea counters read ~/Obsidian/Ideas.md).
export IDEAS_SURFACE_PATH="$SV/Ideas.md"

# Read-only render of the briefing from the scratch vault's current state.
render() {
  "$PY" -c "
import sys; sys.path.insert(0,'$S')
import orchestration_briefing as ob, auto_orchestration as ao
from datetime import datetime, timezone
v='$SV'; cfg=ao.load_config(v)
print(ob.build_briefing(ob.gather_signals(v, cfg, datetime.now(timezone.utc)), cfg), end='')
" 2>/dev/null
}

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

# ── B. briefing signals (read-only render) ──────────────────────────────────
assert_equals "briefing: empty vault renders nothing" "$(render)" ""

mkdir -p "$SV/_inbox"; for i in $(seq 1 10); do echo x > "$SV/_inbox/e$i.md"; done
assert_contains "briefing: inbox over threshold surfaces"       "$(render)" "10 inbox entries to sort"

mkdir -p "$SV/personal/_skill-watchlist/src"
printf -- '---\nstatus: pending-review\nevaluator_classification: HIGH\n---\nb\n' \
  > "$SV/personal/_skill-watchlist/src/p1.md"
assert_contains "briefing: HIGH skill-watchlist surfaces"       "$(render)" "1 HIGH skill-watchlist"

mkdir -p "$SV/personal/_idea-incubator/an-idea"
assert_contains "briefing: incubator idea surfaces"             "$(render)" "1 incubator idea"

# ── C. staged-adapt signal (v4.13.1) surfaces + clears on evaluation ────────
mkdir -p "$SV/_meta/skill-discovery-cache/adapt-state/src"
printf '{}' > "$SV/_meta/skill-discovery-cache/adapt-state/src/newpat.json"
printf '{}' > "$SV/_meta/skill-discovery-cache/adapt-state/evaluated.json"   # root file: must be skipped
assert_contains "briefing: staged adapt candidate surfaces"     "$(render)" "1 skill candidate staged for adapt-evaluation"
printf -- '---\nstatus: pending-review\n---\nb\n' \
  > "$SV/personal/_skill-watchlist/src/newpat.md"   # Pass-2 verdict exists → clears
assert_absent  "briefing: staged adapt clears once evaluated"   "$(render)" "staged for adapt-evaluation"

# ── D. nudges (f + g) ───────────────────────────────────────────────────────
printf -- '---\nstatus: promoted\npromoted_at: 2026-01-01T00:00:00+00:00\n---\nb\n' \
  > "$SV/personal/_skill-watchlist/src/stale.md"
assert_contains "nudge: stale-promotion (>30d) surfaces"        "$(render)" "promoted >30d ago"

TODAY="$(date -u +%Y-%m-%d)"   # today → never idea-ledger-stale; isolates the promote-suggest signal
printf '## %s: Recurring idea\nb\n## %s: Recurring idea\nb\n## %s: Recurring idea\nb\n' "$TODAY" "$TODAY" "$TODAY" \
  > "$SV/Ideas.md"
assert_contains "nudge: promote-suggest (idea x3) surfaces"     "$(render)" "/memory promote"

# ── E. idle chain (dry-run: ordering + bounded flags) ───────────────────────
IDLE="$("$PY" "$S/orchestration_idle.py" --vault-path "$SV" --dry-run 2>/dev/null)"
assert_contains "idle: dry-run status"                          "$IDLE" '"status": "dry-run"'
assert_contains "idle: step 1 reflect-corpus"                   "$IDLE" 'reflect-corpus'
assert_contains "idle: step 2 discover-skills"                  "$IDLE" 'discover-skills'
assert_contains "idle: step 3 adapt-pass1"                      "$IDLE" 'adapt-pass1'
assert_contains "idle: bounded (--max-batches)"                 "$IDLE" '"--max-batches"'
assert_contains "idle: bounded (--limit)"                       "$IDLE" '"--limit"'

# ── F. phase-dispatch (via harness_memory bridge; dry-run) ──────────────────
mkdir -p "$SV/proj/.harness"
hm() { MEMORY_VAULT_PATH="$SV" HARNESS_MEMORY_TOOLKIT_PATH="$S" "$PY" "$HM" "$@" 2>/dev/null; }
PR="$(hm phase-dispatch post-release --project-root "$SV/proj" --dry-run)"
assert_contains "phase: post-release dry-run plan"              "$PR" '"status": "dry-run"'
assert_contains "phase: post-release runs index-skills"         "$PR" 'index_skills.py'
assert_contains "phase: post-release runs discover-skills"      "$PR" 'discover_skills.py'

PW0="$(hm phase-dispatch post-work --project-root "$SV/proj" --dry-run)"
assert_contains "phase: post-work no marker → no-session"       "$PW0" '"status": "no-session"'
printf 'session_id: s\ntranscript: /tmp/t.jsonl\n' > "$SV/proj/.harness/session-id-s.start"
PW1="$(hm phase-dispatch post-work --project-root "$SV/proj" --dry-run)"
assert_contains "phase: post-work single marker → reflect plan" "$PW1" '"status": "dry-run"'
assert_contains "phase: post-work reflect uses --route"         "$PW1" '--route'
printf 'session_id: s2\ntranscript: /tmp/t2.jsonl\n' > "$SV/proj/.harness/session-id-s2.start"
PW2="$(hm phase-dispatch post-work --project-root "$SV/proj" --dry-run)"
assert_contains "phase: 2 markers → ambiguous (concurrency-safe)" "$PW2" '"status": "ambiguous-session"'

# ── G. emit gating (shifted-guard + cooldown) + atomic state ────────────────
# (run last — these mutate the scratch STATE file)
EMIT1="$("$PY" "$S/orchestration_briefing.py" --vault-path "$SV" 2>/dev/null)"
assert_contains "emit: first run emits the briefing block"      "$EMIT1" "MemoryVault — pending"
EMIT2="$("$PY" "$S/orchestration_briefing.py" --vault-path "$SV" 2>/dev/null)"
assert_equals  "emit: second run (cooldown/unchanged) is silent" "$EMIT2" ""
[ -f "$SV/_meta/auto-orchestration-state.json" ] \
  && pass "state: emit records the state file" \
  || fail "state: emit records the state file" "no state file"
TMPS="$(find "$SV/_meta" -maxdepth 1 -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')"
assert_equals  "state: atomic write leaves no .tmp artifact"    "$TMPS" "0"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-v4: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
