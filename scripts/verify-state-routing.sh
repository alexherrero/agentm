#!/usr/bin/env bash
# verify-state-routing.sh — the backend × project-mode matrix + the never-demote
# guard (R1.3 / agentmEngine#1).
#
# Matrix (real CLIs against a scratch vault + scratch device-local project):
#   A. storage.backend resolves to `vault` (via $MEMORY_VAULT_PATH) → state lands
#      at <vault>/projects/<slug>/_harness/, never the repo-local .harness/.
#   B. `.harness/.project-mode=local` opts out even with a vault configured →
#      state lands repo-local, proving the opt-out still wins over a synced backend.
#   C. no backend configured at all (fresh device) → device-local default.
#
# Never-demote (D): `storage.backend=vault` explicitly configured (in
# .agentm-config.json, not just env) but the vault directory doesn't exist →
# `harness_memory.vault_path()` raises `StorageBackendNotInstalledError`,
# `resolve_project()` propagates it (agentmEngine#1: the pre-fix bug was a bare
# `except Exception` here swallowing it and silently returning backend=None,
# i.e. a silent demotion to device-local) — asserted via the real `write-state`
# CLI: non-zero exit, no file ever lands at the device-local fallback path.
#
# VERIFY_STATE_ROUTING_FAULT=1 additionally reproduces the PRE-FIX shape inline
# (a local shadow of the old bare-except resolve_project(), never patching
# production code) and asserts it WOULD have demoted silently — validating that
# this scratch scenario is a faithful fixture for the historical bug. This is
# additive, not a "break something" toggle: every guard in the current fail-loud
# chain (vault_path(), select_backend()'s two raise sites, resolve_project()'s
# re-raise) is now structural, not config-gated, so there is no config knob that
# makes the CURRENT/fixed code demote — the D checks above already prove that
# unconditionally. FAULT=1 is for validating the fixture reproduces the ORIGINAL
# bug shape, not for making today's code fail.
#
# Usage:   bash scripts/verify-state-routing.sh
#          VERIFY_STATE_ROUTING_FAULT=1 bash scripts/verify-state-routing.sh
# Exit:    0 iff every check passes (CI / integration-test friendly).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HM="$REPO/scripts/harness_memory.py"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-state-routing: $PY not found" >&2; exit 2; }

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }

assert_equals() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '[$3]'  got '[$2]'"; fi
}
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}
assert_absent() {
  if [ -e "$2" ]; then fail "$1" "did not expect path: $2"; else pass "$1"; fi
}
assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}

FAULT="${VERIFY_STATE_ROUTING_FAULT:-}"

SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH" 2>/dev/null; rm -rf "$SCRATCH" 2>/dev/null || true; }
trap cleanup EXIT
echo "verify-state-routing: scratch root = $SCRATCH"

SLUG="stateroutedemo"
PLAN_BODY=$'# Plan: fixture\n\n### 1. Task one\n- **Status:** [ ]\n'

# V5-3 deleted the kernel storage_vault.py (the real backend now lives in the
# crickets obsidian-vault plugin). Shim per verify-phases.sh's established
# pattern: a minimal storage_vault.py that re-exports the test-only VaultBackend
# stub, discovered via $OBSIDIAN_VAULT_SCRIPTS.
SHIM="$SCRATCH/vault-plugin"
mkdir -p "$SHIM"
printf 'from vault_backend_stub import VaultBackend\nPROTOCOL = "vault"\n' > "$SHIM/storage_vault.py"

seed_project() {  # seed_project <proj-dir>
  mkdir -p "$1/.harness"
  printf '{"vault_project": "%s"}\n' "$SLUG" > "$1/.harness/project.json"
}

# A blank install-prefix dir (no .agentm-config.json) for the matrix passes —
# `-u AGENTM_INSTALL_PREFIX` would fall through to the operator's real
# $HOME/.claude, which may carry a real vault_path; every invocation below
# must point at an isolated, empty prefix instead. Hermeticity regression:
# this script briefly wrote a fixture into the real vault before this fix.
FRESH_PREFIX="$SCRATCH/install-prefix-fresh"; mkdir -p "$FRESH_PREFIX"
hm() { env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$FRESH_PREFIX" "${MODE_ENV[@]+"${MODE_ENV[@]}"}" \
  HARNESS_MEMORY_TOOLKIT_PATH="$S" OBSIDIAN_VAULT_SCRIPTS="$SHIM" "$PY" "$HM" "$@"; }

if [ "$FAULT" != "1" ]; then
  # ── A. vault backend routes into <vault>/projects/<slug>/_harness/ ─────────
  V_VAULT="$SCRATCH/vault"; mkdir -p "$V_VAULT/projects"
  V_PROJ="$SCRATCH/proj-vault"; seed_project "$V_PROJ"
  MODE_ENV=("MEMORY_VAULT_PATH=$V_VAULT")
  printf '%s' "$PLAN_BODY" | hm write-state --project-root "$V_PROJ" PLAN.md >/dev/null
  assert_exists "A. vault backend: state lands in <vault>/projects/<slug>/_harness/" \
    "$V_VAULT/projects/$SLUG/_harness/PLAN.md"
  assert_absent "A. vault backend: repo-local .harness/ carries no kernel state" \
    "$V_PROJ/.harness/PLAN.md"

  # ── B. .project-mode=local opts out even with a vault configured ───────────
  L_PROJ="$SCRATCH/proj-local-override"; seed_project "$L_PROJ"
  echo "local" > "$L_PROJ/.harness/.project-mode"
  printf '%s' "$PLAN_BODY" | hm write-state --project-root "$L_PROJ" PLAN.md >/dev/null
  assert_exists "B. .project-mode=local: state lands repo-local despite vault configured" \
    "$L_PROJ/.harness/PLAN.md"
  assert_absent "B. .project-mode=local: nothing written under the vault for this project" \
    "$V_VAULT/projects/stateroutedemo-local/_harness/PLAN.md"

  # ── C. no backend configured at all → device-local default ─────────────────
  N_PROJ="$SCRATCH/proj-none"; seed_project "$N_PROJ"
  MODE_ENV=()
  printf '%s' "$PLAN_BODY" | hm write-state --project-root "$N_PROJ" PLAN.md >/dev/null
  assert_exists "C. no backend configured: device-local default write lands" \
    "$N_PROJ/.harness/PLAN.md"
fi

# ── D. never-demote: explicit storage.backend=vault + broken vault path ────
# (unconditional — the permanent CI regression guard for agentmEngine#1)
D_PREFIX="$SCRATCH/install-prefix-broken"; mkdir -p "$D_PREFIX"
D_VAULT="$SCRATCH/vault-broken"   # never created — vault_path() must see it as absent
"$PY" -c "
import json
json.dump({'storage.backend': 'vault', 'plugins.obsidian-vault.vault_path': '$D_VAULT'},
          open('$D_PREFIX/.agentm-config.json', 'w'))
"
D_PROJ="$SCRATCH/proj-broken"; seed_project "$D_PROJ"
hm_broken() { env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$D_PREFIX" \
  HARNESS_MEMORY_TOOLKIT_PATH="$S" OBSIDIAN_VAULT_SCRIPTS="$SHIM" "$PY" "$HM" "$@"; }
D_OUT="$(printf '%s' "$PLAN_BODY" | hm_broken write-state --project-root "$D_PROJ" PLAN.md 2>&1)"
D_RC=$?
assert_equals   "D. never-demote: write-state exits non-zero on broken vault + explicit backend=vault" \
  "$([ "$D_RC" -ne 0 ] && echo yes || echo no)" "yes"
assert_contains "D. never-demote: fail-loud message names the never-demote invariant" \
  "$D_OUT" "never silently demoting"
assert_absent   "D. never-demote: no silent write ever lands at the device-local fallback" \
  "$D_PROJ/.harness/PLAN.md"

if [ "$FAULT" = "1" ]; then
  # ── fixture-validation: the pre-fix bare-except shape really would demote ──
  # Reproduces agentmEngine#1's exact defect (a local shadow — never patches
  # scripts/harness_memory.py) under the SAME broken-vault + explicit-backend
  # scratch scenario as D, to prove this fixture is a faithful regression case
  # for the historical bug (not just an arbitrary broken-config scenario).
  OLD_BUG_OUT="$(AGENTM_INSTALL_PREFIX="$D_PREFIX" env -u MEMORY_VAULT_PATH "$PY" - "$REPO/scripts" "$D_PROJ" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import harness_memory as hm
import backend_selection as bs

def old_buggy_resolve_project(context):
    # agentmEngine#1: this bare `except Exception` (pre-fix) is what swallowed
    # StorageBackendNotInstalledError / StorageSelectionError and silently fell
    # through to backend=None (device-local demotion). Fixed code re-raises.
    try:
        backend = bs.select_backend()
    except Exception:
        backend = None
    return {"backend": backend, "layout": "none" if backend is None else "new"}

result = old_buggy_resolve_project({"cwd": sys.argv[2]})
print("DEMOTED" if result["backend"] is None else "NOT-DEMOTED")
PYEOF
)"
  assert_contains "FAULT: pre-fix bare-except shape reproduces the silent demotion on this fixture" \
    "$OLD_BUG_OUT" "DEMOTED"
fi

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-state-routing: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
