#!/usr/bin/env bash
# verify-memory-roundtrip.sh — end-to-end round-trip of the MemoryVault engine
# on a throwaway fixture vault (Hardening I #45 task 7).
#
# Drives the REAL memory-skill CLIs through a full lifecycle:
#   embed (stub) → save → recall-by-content → reflect → vec-index build →
#   (vec nearest-neighbor read-back) → vault_lint clean
# proving the engine's scripts wire together — the gap the unit suite leaves
# (each tests a function in isolation; nothing exercises save→recall→reflect→
# vec_index→lint as one flow). Mirrors the verify-v4.sh / verify-phases.sh
# PASS/FAIL skeleton.
#
# Hermetic: a `mktemp` vault, no network, no real vault, no sub-agent dispatch.
# Embedding uses the deterministic **stub** mode (`--mode stub`) — a hash-based
# 1024-d vector — so the round-trip never downloads a model.
#
# The vec-index backend (sqlite-vec) needs a Python whose `sqlite3` supports
# `enable_load_extension` (Apple's system Python disables it; CI may not install
# sqlite-vec). When the backend is unavailable the engine graceful-skips to
# keyword recall BY DESIGN — so the nearest-neighbor read-back is asserted only
# when the backend is operational and LOGGED AS SKIPPED otherwise (never silently
# dropped). The build/enqueue path + keyword recall are exercised unconditionally.
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

PASS=0; FAIL=0; SKIP=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }
skip() { RESULTS+=("  SKIP  $1"$'\n'"          ↳ $2"); SKIP=$((SKIP+1)); }

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
ENTRY="$V/personal/reference/deploy-runbook.md"
SAVED_PATH=""
if [ "${VERIFY_MEMORY_FAULT:-}" = "drop-save" ]; then
  :   # negative check: skip the save → downstream recall/lint must fail loudly
else
  SAVED_PATH="$(printf '%s\n' "$SAVE_BODY" | mem save.py reference deploy-runbook \
    --tags "ops,deploy" --body-file - 2>/dev/null)"
fi
assert_exists "save: entry written under the vault" "${SAVED_PATH:-$ENTRY}"
assert_exists "save: entry under personal/reference/" "$ENTRY"

# ── C. recall by content: the saved entry is surfaced (keyword path) ────────
RECALL="$(mem recall.py query "deployment runbook staging gate" -k 5 --mode stub 2>/dev/null)"
assert_contains "recall: saved entry surfaced by content" "$RECALL" "deploy-runbook"

# ── D. reflect: a synthetic transcript is processed + routed ────────────────
printf '%s\n%s\n' \
  '{"type":"user","message":{"role":"user","content":"remember the deploy staging gate"}}' \
  '{"type":"assistant","message":{"role":"assistant","content":"noted the runbook"}}' \
  > "$V/transcript.jsonl"
REFLECT="$(mem reflect.py "$V/transcript.jsonl" --summary --route 2>/dev/null)"; RC=$?
assert_equals  "reflect: --route exits 0 on a vault" "$RC" "0"
assert_contains "reflect: emits a summary pass record" "$REFLECT" '"pass": "summary"'

# ── E. vec-index: build path (enqueue) + nearest-neighbor read-back ─────────
# full-sync --rebuild enqueues every not-indexed entry for re-embed (no
# sqlite-vec needed for the queue) — exercises the embed-enqueue path.
FS="$(mem vec_index.py full-sync --rebuild 2>/dev/null)"
ENQ="$(printf '%s' "$FS" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('enqueued', 0))
except Exception: print(0)" 2>/dev/null)"
if [ "${ENQ:-0}" -ge 1 ] 2>/dev/null; then
  pass "vec-index: saved entry enqueued for embed"
else
  fail "vec-index: saved entry enqueued for embed" "enqueued=$ENQ from: $(printf '%s' "$FS" | cut -c1-160)"
fi
# drain attempts to embed+upsert; exits 0 whether the backend is present or not.
mem vec_index.py drain --mode stub >/dev/null 2>&1
RC=$?
assert_equals "vec-index: drain is non-blocking (exit 0)" "$RC" "0"
# The engine reports whether the vec backend is actually operational here.
SIZE_JSON="$(mem vec_index.py size 2>/dev/null)"
SIZE="$(printf '%s' "$SIZE_JSON" | "$PY" -c "import json,sys
try:
    d=json.load(sys.stdin); s=d.get('size')
    print(s if s is not None else 'null')
except Exception: print('null')" 2>/dev/null)"
if [ "$SIZE" != "null" ] && [ "${SIZE:-0}" -ge 1 ] 2>/dev/null; then
  # Backend operational → assert the nearest-neighbor read-back. Stub embeddings
  # are a hash of the text, so the query must use the entry's OWN indexed
  # embed-text ({slug} [{tags}]\n\n{first_para}, via the engine's extractor) —
  # then the index returns the entry as its nearest neighbor with sim≈1.0. Piped
  # (not via a shell var) so trailing bytes match exactly.
  pass "vec-index: built index holds the seeded entry (size=$SIZE)"
  NN="$("$PY" -c "import sys, pathlib; sys.path.insert(0, '$S'); import vec_index
sys.stdout.write(vec_index._extract_embed_text_from_file(pathlib.Path('$ENTRY')))" 2>/dev/null \
        | mem recall.py query - -k 5 --mode stub 2>/dev/null)"
  SIM="$(printf '%s' "$NN" | "$PY" -c "import json,sys
best=0.0
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try:
        d=json.loads(line)
    except Exception: continue
    if 'deploy-runbook' in (d.get('slug','')+d.get('path','')):
        best=max(best, float(d.get('sim',0) or 0))
print('yes' if best > 0 else 'no')" 2>/dev/null)"
  assert_equals "vec-index: entry returned as its nearest neighbor (sim>0)" "$SIM" "yes"
else
  skip "vec-index: nearest-neighbor read-back" \
       "vec backend unavailable (sqlite-vec extension not loadable on this Python; engine reports: $(printf '%s' "$SIZE_JSON" | cut -c1-100)) — recall verified via the keyword path above"
fi

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
