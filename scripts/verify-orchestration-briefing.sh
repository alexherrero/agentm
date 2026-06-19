#!/usr/bin/env bash
# verify-orchestration-briefing.sh — integration check for the V4 #23
# orchestration-briefing push surface (briefing signals · staged-adapt · nudges).
#
# PM-half: these checks verify orchestration_briefing.py signal-gathering and
# nudge behavior. They travel with the PM-trigger plan in crickets when that
# plan ships — until then they run on the agentm side. Extracted from
# verify-v4.sh by V5-5 task 4.
#
# Runs orchestration_briefing.py against a throwaway scratch vault — never reads
# or writes a real vault, never hits the network, never dispatches a sub-agent.
#
# Usage:   bash scripts/verify-orchestration-briefing.sh
# Exit:    0 iff every check passes (CI / integration-test friendly).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-orchestration-briefing: $PY not found" >&2; exit 2; }

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

echo "verify-orchestration-briefing: scratch vault = $SV"

# Seed config so load_config() has a valid baseline.
"$PY" "$S/auto_orchestration.py" --vault-path "$SV" seed-config >/dev/null 2>&1

# ── B. briefing signals (read-only render) ──────────────────────────────────
assert_equals  "briefing: empty vault renders nothing" "$(render)" ""

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

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-orchestration-briefing: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
