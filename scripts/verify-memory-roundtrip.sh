#!/usr/bin/env bash
# verify-memory-roundtrip.sh — end-to-end round-trip of the MemoryVault engine
# on a throwaway fixture vault (Hardening I #45 task 7).
#
# Drives the REAL memory-skill CLIs through a full lifecycle:
#   embed (stub) → save → recall-by-content → reflect → vault_lint clean
# proving the engine's scripts wire together — the gap the unit suite leaves
# (each tests a function in isolation; nothing exercises save→recall→reflect→
# lint as one flow). Mirrors the verify-v4.sh / verify-phases.sh
# PASS/FAIL skeleton.
#
# Hermetic: a `mktemp` vault, no network, no real vault, no sub-agent dispatch.
# `embed.py` is still exercised in its deterministic **stub** mode
# (`--mode stub`) — a hash-based 1024-d vector, no model download. Nothing in
# recall consumes an embedding any more (the vector index was removed; see
# wiki/designs/agentm-rescope-week1-experiment.md), but `notes_link_discovery
# --embeddings` still does, so the module keeps a round-trip check here.
#
# Usage:   bash scripts/verify-memory-roundtrip.sh
# Exit:    0 iff every (non-skipped) check passes.
#
# Negative check: VERIFY_MEMORY_FAULT=drop-save skips the save so the recall /
# lint assertions fail loudly — proving a broken engine step is caught.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-memory-roundtrip: $PY not found" >&2; exit 2; }

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-memory-roundtrip"
HEALTH_AXIS="memory persist+recall"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0; SKIP=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }
skip() { RESULTS+=("  SKIP  $1"$'\n'"          ↳ $2"); SKIP=$((SKIP+1)); emit_jsonl_check "$1" null; }

assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}
assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}

# ── scratch vault (isolated; auto-removed) ──────────────────────────────────
V="$(mktemp -d)"
# Engine state is machine-scoped (filing-v2 2a): without this export a
# scratch-vault run writes the REAL ~/.local/state/agentm. Hermetic by
# default; phases needing distinct state override per-invocation.
export AGENTM_STATE_DIR="$V/engine-state"
mkdir -p "$AGENTM_STATE_DIR"

cleanup() { rm -rf "$V"; }
trap cleanup EXIT
export MEMORY_VAULT_PATH="$V"
echo "verify-memory-roundtrip: scratch vault = $V"

mem() { "$PY" "$S/$1" "${@:2}"; }   # mem <script> <args...>

# ── A. embed: the stub path builds a deterministic vector (no network) ──────
EMB="$(mem embed.py "deployment runbook staging gate" --mode stub 2>/dev/null)"
EMB_DIM="$(printf '%s' "$EMB" | "$PY" -c "import json,sys
try: print(len(json.load(sys.stdin)))
except Exception: print('ERR')" 2>/dev/null)"
assert_equals "embed: stub mode builds a 1024-d vector" "$EMB_DIM" "1024"

# ── B. save: an entry lands in the vault ────────────────────────────────────
SAVE_BODY="The deployment runbook lives at ops/deploy.md and requires the staging gate before prod."
ENTRY="$V/memory/reference/deploy-runbook.md"
SAVED_PATH=""
if [ "${VERIFY_MEMORY_FAULT:-}" = "drop-save" ]; then
  :   # negative check: skip the save → downstream recall/lint must fail loudly
else
  SAVED_PATH="$(printf '%s\n' "$SAVE_BODY" | mem save.py reference deploy-runbook \
    --tags "ops,deploy" --body-file - 2>/dev/null)"
fi
assert_exists "save: entry written under the vault" "${SAVED_PATH:-$ENTRY}"
assert_exists "save: entry under memory/reference/" "$ENTRY"

# ── C. recall by content: the saved entry is surfaced (keyword path) ────────
RECALL="$(mem recall.py query "deployment runbook staging gate" -k 5 2>/dev/null)"
assert_contains "recall: saved entry surfaced by content" "$RECALL" "deploy-runbook"

# ── C2. kind-scoped recall: --filter kind=<kind> narrows to that kind only ──
# (AA5 consolidation task C3: flips the stale dark-checks.jsonl "kind-scoped
# recall" entry to a live check -- recall.py's parse_filter/_entry_matches_
# filter already support this; nothing here was previously exercised as a
# --jsonl-out record.) A second entry of a DIFFERENT kind, sharing the same
# distinctive phrase, proves the filter narrows by kind rather than content.
# `workflow`, not `howto`: `howto` is retired by the storage rules' deprecation
# map (it collapses into `workflow`), and lint reports a retired value. Any two
# distinct current values prove the same thing this check is after.
SECOND_BODY="This recipe also mentions the deployment runbook staging gate procedure, but it is not the reference entry."
printf '%s\n' "$SECOND_BODY" | mem save.py workflow rotate-api-keys --tags "security" --body-file - >/dev/null 2>&1
KIND_FILTERED="$(mem recall.py query "deployment runbook staging gate" -k 5 --filter "kind=reference" 2>/dev/null)"
if printf '%s' "$KIND_FILTERED" | grep -qF -- "deploy-runbook" && ! printf '%s' "$KIND_FILTERED" | grep -qF -- "rotate-api-keys"; then
  pass "recall: --filter kind=reference includes the matching kind and excludes the other kind"
else
  fail "recall: --filter kind=reference includes the matching kind and excludes the other kind" \
       "got: $(printf '%s' "$KIND_FILTERED" | tr '\n' '~' | cut -c1-200)"
fi

# ── D. reflect: a synthetic transcript is processed + routed ────────────────
printf '%s\n%s\n' \
  '{"type":"user","message":{"role":"user","content":"remember the deploy staging gate"}}' \
  '{"type":"assistant","message":{"role":"assistant","content":"noted the runbook"}}' \
  > "$V/transcript.jsonl"
REFLECT="$(mem reflect.py "$V/transcript.jsonl" --summary --route 2>/dev/null)"; RC=$?
assert_equals  "reflect: --route exits 0 on a vault" "$RC" "0"
assert_contains "reflect: emits a summary pass record" "$REFLECT" '"pass": "summary"'

# ── F. vault_lint: the round-trip left the vault clean ──────────────────────
LINT="$(mem vault_lint.py --format json 2>/dev/null)"; RC=$?
assert_equals "lint: vault_lint exits 0" "$RC" "0"
NFIND="$(printf '%s' "$LINT" | "$PY" -c "import json,sys
try: print(len(json.load(sys.stdin).get('findings', [])))
except Exception: print('ERR')" 2>/dev/null)"
assert_equals "lint: vault is clean (0 findings)" "$NFIND" "0"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-memory-roundtrip: $PASS passed, $FAIL failed, $SKIP skipped"
[ "$FAIL" -eq 0 ]
