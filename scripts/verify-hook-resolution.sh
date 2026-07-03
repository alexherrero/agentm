#!/usr/bin/env bash
# verify-hook-resolution.sh — regression guard for the hook config dual-key
# read (R1.2 / agentmEngine#0).
#
# Pre-fix, the four memory hooks' `_resolve_vault_path()` only checked a bare
# `vault_path` key in `.agentm-config.json`. The real config written by
# `agentm_config.py --vault-path` stores it under the plugin-namespaced key
# `plugins.obsidian-vault.vault_path` — so a fresh config with ONLY that key
# silently failed to resolve, and every hook no-op'd without a trace. R0's fix
# made the read dual-key: `d.get("plugins.obsidian-vault.vault_path") or
# d.get("vault_path")`. This script is the permanent regression guard.
#
# Builds a scratch HOME (`$HOME` — the hooks resolve config/scripts off
# `$HOME/.claude` and compute the transcript path off raw `$HOME`, not
# `AGENTM_INSTALL_PREFIX`) with a `.agentm-config.json` carrying ONLY the
# plugin-namespaced key (+ `source_clones.agentm` so the hooks find the
# scripts without a real skill install) and a scratch vault seeded with an
# always-load entry + a personal/reference entry. Runs all four hook scripts
# against it (env -u MEMORY_VAULT_PATH — the config-read path must be what
# resolves the vault, not a leaked env var) and asserts each one actually used
# the vault:
#   - recall session-start:  always-load entry appears on stdout
#   - recall prompt-submit:  a matching query surfaces the reference entry
#   - reflect-stop:          --route succeeds; a real record lands on stdout
#   - reflect-idle:          a stale orphan marker is renamed .start→.reflected
#     (MEMORY_IDLE_THRESHOLD_SEC=0 makes a fresh marker instantly "stale" so
#     the test doesn't need to fake mtimes)
#
# The assertions below are always the POSITIVE expectations (vault resolves;
# each hook produces real, vault-derived output). Mirrors the
# VERIFY_MEMORY_FAULT convention in verify-memory-roundtrip.sh: fault mode
# doesn't swap in different expected values, it deliberately breaks the setup
# (strips the plugin-namespaced key from config entirely — no vault key of any
# kind) so the SAME assertions fail loudly, proving they have teeth. Run
# normally (no fault var), every assertion should pass on the fixed hooks; run
# under VERIFY_HOOK_RESOLUTION_FAULT=1, every assertion should fail — that's
# the fault-injection mode "detecting" what agentmEngine#0 actually looked
# like (a config with the real vault key present in its real location, but
# nothing resolving because the reader hadn't been taught to look for it).
#
# Usage:   bash scripts/verify-hook-resolution.sh
#          VERIFY_HOOK_RESOLUTION_FAULT=1 bash scripts/verify-hook-resolution.sh
# Exit:    0 iff every check passes (CI / integration-test friendly); under
#          FAULT=1 every check is EXPECTED to fail (non-zero exit proves the
#          fault-injection mode has teeth — see Verification in the plan).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HOOKS="$REPO/harness/hooks"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-hook-resolution: $PY not found" >&2; exit 2; }

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }

assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}
assert_absent() {
  if [ -e "$2" ]; then fail "$1" "did not expect path: $2"; else pass "$1"; fi
}

# ── scratch HOME + vault (isolated; auto-removed) ───────────────────────────
SCRATCH_HOME="$(mktemp -d)"
SV="$(mktemp -d)"
PROJ="$(mktemp -d)"
# reflect-idle backgrounds detached (reparented) orchestration_idle.py /
# vec_index.py drain jobs when MEMORY_VAULT_PATH resolves — fire-and-forget by
# design, so cleanup can race them mid-write. Best-effort, quiet, retried once.
cleanup() { rm -rf "$SCRATCH_HOME" "$SV" "$PROJ" 2>/dev/null; rm -rf "$SCRATCH_HOME" "$SV" "$PROJ" 2>/dev/null || true; }
trap cleanup EXIT
echo "verify-hook-resolution: scratch HOME=$SCRATCH_HOME vault=$SV project=$PROJ"

mkdir -p "$SCRATCH_HOME/.claude"

FAULT="${VERIFY_HOOK_RESOLUTION_FAULT:-}"

# Baseline config carries ONLY the plugin-namespaced key (the real shape
# agentm_config.py --vault-path writes) + source_clones.agentm (so the hooks
# find the memory scripts without a real skill install — keeps a fault-mode
# failure attributable to vault resolution, not to the script being missing).
# FAULT=1 strips the vault key entirely, leaving source_clones.agentm alone.
if [ "$FAULT" = "1" ]; then
  "$PY" -c "
import json
json.dump({'source_clones': {'agentm': '$REPO'}},
          open('$SCRATCH_HOME/.claude/.agentm-config.json', 'w'))
"
else
  "$PY" -c "
import json
json.dump({'plugins.obsidian-vault.vault_path': '$SV', 'source_clones': {'agentm': '$REPO'}},
          open('$SCRATCH_HOME/.claude/.agentm-config.json', 'w'))
"
fi

# ── seed the scratch vault ──────────────────────────────────────────────────
mkdir -p "$SV/personal/_always-load"
printf -- '---\nkind: convention\ntags: []\n---\nHOOK-RESOLUTION-CANARY: always-load entry loaded via the dual-key vault_path read.\n' \
  > "$SV/personal/_always-load/hook-resolution-canary.md"

mkdir -p "$SV/personal/reference"
printf -- '---\nkind: reference\ntags: [hook-resolution]\n---\nHOOK-RESOLUTION-CANARY reference entry: deploy runbook staging gate lives here.\n' \
  > "$SV/personal/reference/hook-resolution-canary-ref.md"

SESSION_ID="00000000-0000-0000-0000-000000000001"
CWD_SLUG="-$(printf '%s' "$PROJ" | tr '/' '-')"
mkdir -p "$SCRATCH_HOME/.claude/projects/$CWD_SLUG"
TRANSCRIPT="$SCRATCH_HOME/.claude/projects/$CWD_SLUG/$SESSION_ID.jsonl"
printf '%s\n%s\n' \
  '{"type":"user","message":{"role":"user","content":"remember the HOOK-RESOLUTION-CANARY deploy staging gate"}}' \
  '{"type":"assistant","message":{"role":"assistant","content":"noted"}}' \
  > "$TRANSCRIPT"

run_hook() {  # run_hook <hook-script-relpath> [stdin] — stdout only
  ( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX \
      bash "$HOOKS/$1" <<<"${2:-}" 2>/dev/null )
}

# ── A. recall session-start: always-load entry on stdout ───────────────────
SS_OUT="$(run_hook memory-recall-session-start/memory-recall-session-start.sh \
  "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJ\"}")"
assert_contains "session-start: always-load entry resolved from the scratch vault" "$SS_OUT" "HOOK-RESOLUTION-CANARY"

# ── B. recall prompt-submit: matching query surfaces the reference entry ───
PS_OUT="$(run_hook memory-recall-prompt-submit/memory-recall-prompt-submit.sh \
  '{"hookEventName":"UserPromptSubmit","prompt":"what do you know about the HOOK-RESOLUTION-CANARY deploy staging gate"}')"
assert_contains "prompt-submit: reference entry resolved from the scratch vault" "$PS_OUT" "hook-resolution-canary-ref"

# ── C. reflect-stop: --route succeeds; a real record lands on stdout ───────
RS_OUT="$(run_hook memory-reflect-stop/memory-reflect-stop.sh \
  "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJ\"}")"
assert_contains "reflect-stop: --route emits a summary pass record" "$RS_OUT" '"pass": "summary"'

# ── D. reflect-idle: orphan marker renamed .start→.reflected iff resolvable ─
rm -rf "$PROJ/.harness"; mkdir -p "$PROJ/.harness"
MARKER="$PROJ/.harness/session-id-$SESSION_ID.start"
cat > "$MARKER" <<EOF
session_id: $SESSION_ID
started_at: 2026-01-01T00:00:00Z
transcript: $TRANSCRIPT
EOF
( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX MEMORY_IDLE_THRESHOLD_SEC=0 \
    bash "$HOOKS/memory-reflect-idle/memory-reflect-idle.sh" >/dev/null 2>&1 )
assert_absent "reflect-idle: orphan marker consumed (.start removed)" "$MARKER"
assert_exists "reflect-idle: orphan marker reflected (.reflected written)" "${MARKER%.start}.reflected"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-hook-resolution: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
