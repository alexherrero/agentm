#!/usr/bin/env bash
# verify-reflection.sh — the tri-lane reflection routing + the machine-source
# filter (R1.5 / agentmExperience#2, R0.3 fix).
#
# Two independent invariants, tested at the layer where each is actually
# enforced:
#
#   1. Classification (mine_transcript, called directly — mode-independent):
#      a genuine operator preference statement ("I prefer X") classifies
#      HIGH; a junk corpus of bare always/never phrasing ("it always
#      crashes", "this was never touched") never classifies HIGH — R0.3
#      demoted the bare always/never pattern out of the HIGH lane precisely
#      because it fires on ordinary discussion, not just operator directives.
#      (The plan's original "HIGH-bait always X" framing predates this
#      demotion; adapted here to a still-HIGH pattern — see note below.)
#
#   2. Source filtering (reflect.py corpus, real CLI, all 3 route modes):
#      a transcript under a `subagents/` path — even carrying the SAME
#      still-HIGH preference pattern, repeated, so it would unambiguously
#      classify HIGH if mined — must never contribute anything to the vault.
#      `_discover_transcripts` excludes `subagents/` before `mine_transcript`
#      ever runs on it; this is mode-independent (HIGH auto-saves regardless
#      of --route-mode), so run under auto/silent/interactive to prove the
#      filter, not the mode, is what's protecting the vault.
#
# VERIFY_REFLECTION_FAULT=1 calls mine_transcript() directly on the
# machine-source fixture (bypassing _discover_transcripts entirely — the
# pre-filter corpus) and asserts it WOULD classify HIGH, proving the fixture
# is a faithful trigger for the historical junk-slug pollution the filter
# exists to prevent.
#
# Hermetic: MEMORY_TRANSCRIPT_ROOT points at a scratch dir; MEMORY_VAULT_PATH
# at a scratch vault. No network, no real ~/.claude/projects/ read.
#
# Usage:   bash scripts/verify-reflection.sh
#          VERIFY_REFLECTION_FAULT=1 bash scripts/verify-reflection.sh
# Exit:    0 iff every check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-reflection: $PY not found" >&2; exit 2; }

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }

assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}
assert_absent_in_tree() {  # assert_absent_in_tree <desc> <root> <needle>
  if grep -qrF -- "$3" "$2" 2>/dev/null; then
    fail "$1" "found '$3' somewhere under $2 — it should never have landed"
  else
    pass "$1"
  fi
}

ROOT="$(mktemp -d)"
cleanup() { rm -rf "$ROOT" 2>/dev/null || true; }
trap cleanup EXIT
echo "verify-reflection: scratch root = $ROOT"

# ── fixture transcripts ─────────────────────────────────────────────────────
# Still-HIGH pattern: "I prefer X" (_PREFERENCE_PATTERNS) — unlike the bare
# always/never pattern, R0.3 did NOT demote this one. Using it (rather than
# the plan's literal "always X" example) for the machine-source fixture keeps
# the filter test meaningful: unfiltered, this text unambiguously classifies
# HIGH, so its absence from the vault proves the filter, not a classification
# side effect.
OPERATOR_PREF="I prefer kebab-case slugs for every memory entry."
MACHINE_BAIT="I prefer using recursive-descent for this parser."
JUNK_LINES=(
  "the deploy job always crashes on a cold cache"
  "this code path was never touched after the rewrite"
  "tests always fail on the first run in this repo"
)

write_transcript() {  # write_transcript <path> <user-line> [<user-line> ...]
  mkdir -p "$(dirname "$1")"
  : > "$1"
  for line in "${@:2}"; do
    "$PY" -c "
import json
print(json.dumps({'type':'user','message':{'role':'user','content':'''$line'''}}))
" >> "$1"
  done
}

SESSION_DIR="$ROOT/-scratch-project/session-op"
OP_TRANSCRIPT="$SESSION_DIR/transcript.jsonl"
MACHINE_TRANSCRIPT="$SESSION_DIR/subagents/sub-transcript.jsonl"
write_transcript "$OP_TRANSCRIPT" "$OPERATOR_PREF" "${JUNK_LINES[@]}"
write_transcript "$MACHINE_TRANSCRIPT" "$MACHINE_BAIT" "$MACHINE_BAIT" "$MACHINE_BAIT"

FAULT="${VERIFY_REFLECTION_FAULT:-}"

if [ "$FAULT" = "1" ]; then
  # ── fault: the pre-filter corpus — mine the machine transcript directly ──
  OUT="$("$PY" -c "
import sys, pathlib; sys.path.insert(0, '$S')
import reflect
r = reflect.mine_transcript(pathlib.Path('$MACHINE_TRANSCRIPT'))
confidences = sorted(c.confidence for c in r['memory_candidates'])
print(','.join(confidences))
" 2>&1)"
  assert_contains "fault: unfiltered machine-source bait classifies HIGH (fixture is faithful)" "$OUT" "HIGH"
else
  # ── 1. classification: genuine HIGH vs the always/never junk corpus ──────
  MINE_OUT="$("$PY" -c "
import sys, json, pathlib; sys.path.insert(0, '$S')
import reflect
r = reflect.mine_transcript(pathlib.Path('$OP_TRANSCRIPT'))
print(json.dumps([{'slug': c.slug, 'confidence': c.confidence, 'body': c.body} for c in r['memory_candidates']]))
" 2>&1)"
  HIGH_SLUGS="$("$PY" -c "
import json
cands = json.loads('''$MINE_OUT''')
print(','.join(c['slug'] for c in cands if c['confidence'] == 'HIGH'))
" 2>/dev/null)"
  assert_contains "1. genuine operator preference classifies HIGH" "$HIGH_SLUGS" "kebab"
  JUNK_HIGH_COUNT="$("$PY" -c "
import json
cands = json.loads('''$MINE_OUT''')
junk_markers = ('crash', 'never touched', 'always fail')
hits = [c for c in cands if c['confidence'] == 'HIGH' and any(m in c['body'].lower() for m in junk_markers)]
print(len(hits))
" 2>/dev/null)"
  assert_equals "1. junk-slug corpus (always/never phrasing): none classify HIGH" "${JUNK_HIGH_COUNT:-ERR}" "0"

  # ── 2. source filter, all three route modes ──────────────────────────────
  for mode in auto silent interactive; do
    V="$(mktemp -d)"
    CORPUS_OUT="$(env MEMORY_VAULT_PATH="$V" "$PY" "$S/reflect.py" corpus \
      --projects-root "$ROOT" --vault-path "$V" --execute --route-mode "$mode" 2>&1)"
    TOTAL="$("$PY" -c "
import json,sys
try: print(json.loads('''$CORPUS_OUT'''.splitlines()[-1] if False else '''$CORPUS_OUT''').get('total_transcripts'))
except Exception:
    import re
    m = re.search(r'\"total_transcripts\":\s*(\d+)', '''$CORPUS_OUT''')
    print(m.group(1) if m else 'ERR')
" 2>/dev/null)"
    assert_equals "2. [$mode] discovery finds exactly 1 transcript (subagents/ excluded)" "${TOTAL:-ERR}" "1"
    assert_absent_in_tree "2. [$mode] machine-source bait never lands anywhere in the vault" "$V" "recursive-descent"
    assert_contains "2. [$mode] operator's genuine preference IS saved to the vault" \
      "$(grep -rl "kebab-case" "$V" 2>/dev/null || true)" "$V"
    rm -rf "$V"
  done
fi

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-reflection: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
