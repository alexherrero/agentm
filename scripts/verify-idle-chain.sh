#!/usr/bin/env bash
# verify-idle-chain.sh — the idle chain and the post-release dispatch, run for
# real and asserted on what they actually changed (Hardening I #45 follow-up,
# issue #71).
#
# WHY THIS EXISTS. verify-v4.sh section E drives `orchestration_idle.py
# --dry-run` and asserts `"status": "dry-run"` plus the step names. That proves
# the chain PLANS correctly and nothing more: every step could no-op forever and
# the gate would stay green. That is the V4 #39 bug class — a component that
# lands and silently does nothing — and it is exactly what this file closes. The
# dry-run checks in verify-v4.sh keep their job (cheap plan/ordering assertions);
# this file drives the same chain WITHOUT --dry-run and asserts the mutations:
# the memory entry that lands, the seen-state that advances, the candidate JSONs
# that appear, the cooldown that is recorded and then honored.
#
# Every assertion pins to observed reality — a file that exists, a literal from
# the fixture, a count from `find`. None of them recompute an expectation with
# the implementation's own formula, because a check that derives its expectation
# from the code under test only ever proves the two agree.
#
# HERMETIC. Scratch vault + scratch transcript root under one `mktemp -d`, and
# no network:
#   • MEMORY_TRANSCRIPT_ROOT points at the fixture tree. The chain invokes
#     `reflect.py corpus` WITHOUT --projects-root, so this env var is the only
#     thing standing between the gate and the operator's real ~/.claude/projects.
#     Scenario A asserts the seen-state names exactly the fixture session, which
#     is what catches that plumbing breaking.
#   • The discovery whitelist is seeded comments-only (zero URLs) and the cadence
#     state is seeded fresh, so `discover_skills.py --cadence-check` returns
#     before it fetches anything. Both are seeded: cadence short-circuits first,
#     the empty whitelist covers a machine whose clock makes cadence miss.
#   • The adapt fixture carries no GitHub links, so Pass-1 enrichment never
#     reaches api.github.com.
#
# Usage:   bash scripts/verify-idle-chain.sh
# Exit:    0 iff every check passes (CI / integration-test friendly).
#
# Negative check: VERIFY_IDLE_CHAIN_FAULT=dry-run runs scenario A's chain WITH
# --dry-run — the precise regression this gate exists to catch, a chain that
# plans and mutates nothing. Every scenario-A mutation assertion MUST fail under
# it. CI runs WITHOUT the fault.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-idle-chain: $PY not found" >&2; exit 2; }

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-idle-chain"
HEALTH_AXIS="capability function"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

# assert_equals <desc> <actual> <expected>
assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
# assert_exists <desc> <path>
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}
# assert_absent <desc> <path>
assert_absent() {
  if [ -e "$2" ]; then fail "$1" "did not expect path: $2"; else pass "$1"; fi
}
# assert_in_tree <desc> <root> <needle> — some file under <root> contains <needle>
assert_in_tree() {
  if grep -qrF -- "$3" "$2" 2>/dev/null; then pass "$1"
  else fail "$1" "no file under $2 contains '$3'"; fi
}

# jfield <json> <dotted.path> — pull one field exactly (a bare grep would match
# the same key name at two nesting levels).
jfield() {
  printf '%s' "$1" | "$PY" -c "
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print(''); raise SystemExit(0)
for k in '$2'.split('.'):
    d = d.get(k) if isinstance(d, dict) else None
print('' if d is None else d)
"
}
# step_outcome <json> <step-name>
step_outcome() {
  printf '%s' "$1" | "$PY" -c "
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print(''); raise SystemExit(0)
for s in d.get('steps', []):
    if s.get('name') == '$2':
        print(s.get('outcome', '')); raise SystemExit(0)
print('')
"
}
# count_files <dir> <glob>
count_files() {
  if [ -d "$1" ]; then find "$1" -type f -name "$2" 2>/dev/null | wc -l | tr -d ' '
  else echo 0; fi
}

SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
echo "verify-idle-chain: scratch root = $SCRATCH"

FAULT="${VERIFY_IDLE_CHAIN_FAULT:-}"
DRY_FLAG=()
[ "$FAULT" = "dry-run" ] && DRY_FLAG=(--dry-run)

# ── fixture literals (hand-written; every assertion below pins to these) ─────
OPERATOR_PREF="I prefer kebab-case slugs for every memory entry."
PREF_NEEDLE="kebab-case slugs"
SESSION_KEY="session-a/transcript"

# seed_vault <vault-dir> — a scratch vault primed for a hermetic chain run.
seed_vault() {
  local v="$1"
  mkdir -p "$v/memory" "$v/_meta/skill-discovery-cache/fixture-source"
  # Comments-only whitelist: discover_skills.py seeds its 4 network sources only
  # when this file is absent, so writing it first is what keeps the run offline.
  printf '# Skill-discovery sources\n# verify-idle-chain fixture: intentionally no URLs.\n' \
    > "$v/memory/skill-discovery-sources.md"
  # Fresh cadence state so --cadence-check returns before fetching. The
  # schema_version key is load-bearing: _load_state discards any state without
  # it, which silently re-enables the network.
  "$PY" -c "
import json, datetime
from pathlib import Path
now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
Path('$v/_meta/skill-discovery-cache/state.json').write_text(
    json.dumps({'schema_version': 1, 'last_scan': now, 'sources': {}}), encoding='utf-8')
"
  # Two adapt candidates, no GitHub links anywhere → Pass-1 stages both without
  # touching api.github.com. Headings drive the staged filenames asserted below.
  cat > "$v/_meta/skill-discovery-cache/fixture-source/diff-2026-01-01.md" <<'EOF'
## Scratch Fixture Pattern Alpha
A local-only fixture candidate carrying no external links whatsoever.
## Scratch Fixture Pattern Beta
A second local-only fixture candidate carrying no external links whatsoever.
EOF
}

# seed_transcripts <root> — one session carrying a genuine HIGH operator
# preference, so reflect-corpus has something real to mine and save.
seed_transcripts() {
  local dir="$1/-scratch-proj/session-a"
  mkdir -p "$dir"
  "$PY" -c "
import json
from pathlib import Path
Path('$dir/transcript.jsonl').write_text(
    json.dumps({'type': 'user', 'message': {'role': 'user', 'content': '''$OPERATOR_PREF'''}}) + '\n',
    encoding='utf-8')
"
}

# ── A. the real chain: run it, then assert what changed ─────────────────────
echo "verify-idle-chain: ── A. non-dry-run chain ──"
A_VAULT="$SCRATCH/a-vault"; A_TR="$SCRATCH/a-transcripts"
seed_vault "$A_VAULT"; seed_transcripts "$A_TR"
A_ADAPT="$A_VAULT/_meta/skill-discovery-cache/adapt-state/fixture-source"

A_OUT="$(env MEMORY_VAULT_PATH="$A_VAULT" MEMORY_TRANSCRIPT_ROOT="$A_TR" \
  "$PY" "$S/orchestration_idle.py" --vault-path "$A_VAULT" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} 2>/dev/null)"

assert_equals "A. chain reports it ran (not a plan)" "$(jfield "$A_OUT" status)" "ran"

# reflect-corpus really mined the fixture and saved the operator's preference.
assert_in_tree "A. reflect: the operator preference landed in the vault" "$A_VAULT" "$PREF_NEEDLE"
assert_exists  "A. reflect: seen-state file written" "$A_VAULT/_meta/transcript-reflection-state.json"
# Hermeticity: MEMORY_TRANSCRIPT_ROOT is honored, so exactly the fixture session
# was processed. If the env var stopped reaching the subprocess this would be
# the operator's whole transcript history instead.
A_SESSIONS="$("$PY" -c "
import json
from pathlib import Path
p = Path('$A_VAULT/_meta/transcript-reflection-state.json')
try:
    print(','.join(sorted(json.loads(p.read_text())['sessions'])))
except Exception:
    print('ERR')
" 2>/dev/null)"
assert_equals "A. reflect: seen-state names exactly the fixture session" "$A_SESSIONS" "$SESSION_KEY"

# discover-skills throttled on the seeded cadence — and said so truthfully.
assert_equals "A. discover: step reports throttled" "$(step_outcome "$A_OUT" discover-skills)" "throttled"

# adapt-pass1 really staged both fixture candidates, under the names the
# fixture headings imply.
assert_exists "A. adapt: alpha candidate staged" "$A_ADAPT/scratch-fixture-pattern-alpha.json"
assert_exists "A. adapt: beta candidate staged"  "$A_ADAPT/scratch-fixture-pattern-beta.json"
A_STAGED_ON_DISK="$(count_files "$A_VAULT/_meta/skill-discovery-cache/adapt-state/fixture-source" '*.json')"
assert_equals "A. adapt: exactly 2 candidates on disk" "$A_STAGED_ON_DISK" "2"
# The count the chain REPORTS must match the files that actually exist.
assert_equals "A. adapt: reported staged_candidates matches the files on disk" \
  "$(jfield "$A_OUT" staged_candidates)" "$A_STAGED_ON_DISK"

# The cooldown was recorded — the state write that makes run 2 a no-op.
A_FIRE="$("$PY" -c "
import json
from pathlib import Path
p = Path('$A_VAULT/_meta/auto-orchestration-state.json')
try:
    print('yes' if json.loads(p.read_text())['last_fire']['idle_chain'] else 'no')
except Exception:
    print('no')
" 2>/dev/null)"
assert_equals "A. state: last_fire recorded for idle_chain" "$A_FIRE" "yes"

# ── B. the cooldown actually gates the next real run ────────────────────────
# Only meaningful once A really fired, so skip it under the fault.
if [ "$FAULT" != "dry-run" ]; then
  echo "verify-idle-chain: ── B. cooldown gates run 2 ──"
  B_OUT="$(env MEMORY_VAULT_PATH="$A_VAULT" MEMORY_TRANSCRIPT_ROOT="$A_TR" \
    "$PY" "$S/orchestration_idle.py" --vault-path "$A_VAULT" 2>/dev/null)"
  assert_equals "B. second run inside the window is a cooldown no-op" \
    "$(jfield "$B_OUT" status)" "cooldown"
  assert_equals "B. cooldown run stages nothing new" \
    "$(count_files "$A_VAULT/_meta/skill-discovery-cache/adapt-state/fixture-source" '*.json')" "2"
fi

# ── C. the disable toggle really stops the chain ────────────────────────────
echo "verify-idle-chain: ── C. enable_idle_chain=false ──"
C_VAULT="$SCRATCH/c-vault"; C_TR="$SCRATCH/c-transcripts"
seed_vault "$C_VAULT"; seed_transcripts "$C_TR"
"$PY" "$S/auto_orchestration.py" --vault-path "$C_VAULT" seed-config >/dev/null 2>&1
"$PY" -c "
import re, sys
from pathlib import Path
p = Path('$C_VAULT/memory/auto-orchestration-config.md')
text, n = re.subn(r'enable_idle_chain\s*=\s*\w+', 'enable_idle_chain = false', p.read_text())
if n != 1:
    sys.exit('fixture: expected exactly 1 enable_idle_chain key in the seeded config, found %d' % n)
p.write_text(text, encoding='utf-8')
" || fail "C. fixture: could not flip enable_idle_chain in the seeded config" "see stderr"
C_OUT="$(env MEMORY_VAULT_PATH="$C_VAULT" MEMORY_TRANSCRIPT_ROOT="$C_TR" \
  "$PY" "$S/orchestration_idle.py" --vault-path "$C_VAULT" 2>/dev/null)"
assert_equals "C. disabled chain reports disabled" "$(jfield "$C_OUT" status)" "disabled"
assert_absent "C. disabled chain mines no transcripts" "$C_VAULT/_meta/transcript-reflection-state.json"
assert_equals "C. disabled chain stages no candidates" \
  "$(count_files "$C_VAULT/_meta/skill-discovery-cache/adapt-state" '*.json')" "0"

# ── D. the discover-skills outcome label tells the truth ────────────────────
# Regression guard for the label bug this gate found: the outcome was derived by
# grepping the step's stdout for "cadence" + "skip", which matched the
# `"cadence_skipped": false` KEY — so a run that fetched every network source
# still reported "throttled". Same fixture as A but with no cadence state, so
# the gate is genuinely open; the whitelist is still empty, so nothing is
# fetched and the run stays offline. A truthful label is anything but
# "throttled".
echo "verify-idle-chain: ── D. discover outcome is not hardcoded ──"
D_VAULT="$SCRATCH/d-vault"; D_TR="$SCRATCH/d-transcripts"
seed_vault "$D_VAULT"; seed_transcripts "$D_TR"
rm -f "$D_VAULT/_meta/skill-discovery-cache/state.json"
D_OUT="$(env MEMORY_VAULT_PATH="$D_VAULT" MEMORY_TRANSCRIPT_ROOT="$D_TR" \
  "$PY" "$S/orchestration_idle.py" --vault-path "$D_VAULT" 2>/dev/null)"
D_DISCOVER="$(step_outcome "$D_OUT" discover-skills)"
if [ -n "$D_DISCOVER" ] && [ "$D_DISCOVER" != "throttled" ]; then
  pass "D. an un-throttled discover step is not labelled throttled"
else
  fail "D. an un-throttled discover step is not labelled throttled" \
    "cadence state was removed, so the step could not have throttled — got '[$D_DISCOVER]'"
fi

# ── E. post-release phase dispatch, for real ───────────────────────────────
# verify-phases.sh drives post-release in --dry-run only, so the same
# plans-but-never-does gap applies to it. Run it for real and assert the skill
# index entry lands. index_skills.py needs MEMORY_SKILL_PATHS (the dispatch's
# argv passes only --vault-path), so the fixture supplies a scratch skill.
echo "verify-idle-chain: ── E. non-dry-run post-release dispatch ──"
E_VAULT="$SCRATCH/e-vault"; seed_vault "$E_VAULT"
E_SKILLS="$SCRATCH/e-skills/fixture-skill"; mkdir -p "$E_SKILLS"
cat > "$E_SKILLS/SKILL.md" <<'EOF'
---
name: fixture-skill
description: A scratch skill that exists only to prove the index write happened.
version: 1.0.0
---

# fixture-skill

Scratch fixture body.
EOF
# --project-root is scratch on purpose: it defaults to "." and the sibling
# crystallization step reads <project-root>/.harness/, so leaving it unset would
# have this gate reading the repo it is running inside.
E_PROJ="$SCRATCH/e-proj"; mkdir -p "$E_PROJ/.harness"
E_OUT="$(env MEMORY_VAULT_PATH="$E_VAULT" MEMORY_SKILL_PATHS="$SCRATCH/e-skills" \
  "$PY" "$S/orchestration_phase.py" --vault-path "$E_VAULT" --project-root "$E_PROJ" \
  post-release 2>/dev/null)"
assert_equals "E. post-release reports it ran" "$(jfield "$E_OUT" status)" "ran"
E_INDEXED="$(count_files "$E_VAULT/personal-skills" 'fixture-skill.md')"
assert_equals "E. index-skills wrote the skill-pointer entry" "$E_INDEXED" "1"
assert_in_tree "E. the indexed entry points back at the fixture source" \
  "$E_VAULT/personal-skills" "fixture-skill"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-idle-chain: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
