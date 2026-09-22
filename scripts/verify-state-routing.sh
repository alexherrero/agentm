#!/usr/bin/env bash
# verify-state-routing.sh — the backend × project-mode matrix + the never-demote
# guard (R1.3 / agentmEngine#1).
#
# Matrix (real CLIs against a scratch vault + scratch device-local project). In
# each case a named plan is resolved the way /plan resolves it, its progress log
# is seeded where the resolver points, and the kernel's append-progress writes
# to it — so both the path and the kernel write are proven per mode:
#   A. storage.backend resolves to `vault` (via $MEMORY_ROOT) → the plan is a
#      task, <vault>/desk/projects/<slug>/tasks/NNN-<slug>/, never the repo-local
#      .harness/; a bare call names no plan (exit 4). A2 does the same for the
#      vault-root projects/ generation.
#   B. `.harness/.project-mode=local` opts out even with a vault configured →
#      the repo-local pair, proving the opt-out still wins over a synced backend.
#   C. no backend configured at all (fresh device) → the repo-local pair.
#
# Never-demote (D): `storage.backend=vault` explicitly configured (in
# .agentm-config.json, not just env) but the vault directory doesn't exist →
# `harness_memory.vault_path()` raises `StorageBackendNotInstalledError`,
# `resolve_project()` propagates it (agentmEngine#1: the pre-fix bug was a bare
# `except Exception` here swallowing it and silently returning backend=None,
# i.e. a silent demotion to device-local) — asserted via the real
# `resolve-active-plan` and `append-progress` CLIs: non-zero exit, no
# device-local path answered, nothing written at the device-local fallback.
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

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-state-routing"
HEALTH_AXIS="safety/recoverability"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

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
# Engine state is machine-scoped (filing-v2 2a): without this export a
# scratch-vault run writes the REAL ~/.local/state/agentm. Hermetic by
# default; phases needing distinct state override per-invocation.
export AGENTM_STATE_DIR="$SCRATCH/engine-state"
mkdir -p "$AGENTM_STATE_DIR"
# The append-progress cases take the vault mutex, whose lock directory sits under
# $XDG_CACHE_HOME/agentm/locks, ~/.cache by default. Left there, every run of
# this gate left a hash-named directory per fixture vault in the operator's
# own cache.
export XDG_CACHE_HOME="$SCRATCH/cache"

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
hm() { env -u MEMORY_ROOT -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$FRESH_PREFIX" "${MODE_ENV[@]+"${MODE_ENV[@]}"}" \
  HARNESS_MEMORY_TOOLKIT_PATH="$S" OBSIDIAN_VAULT_SCRIPTS="$SHIM" "$PY" "$HM" "$@"; }

if [ "$FAULT" != "1" ]; then
  # route <label> <proj> <expect-plan> <expect-progress> <absent-path>
  #   Resolve the named plan the way /plan does, seed its progress log at the
  #   path the resolver named (the plugin writes files where agentm points),
  #   then append through the kernel's append-progress: the resolved path and
  #   the kernel write must both land where the mode says, and nothing lands at
  #   <absent-path>.
  route() {
    local label="$1" proj="$2" want_plan="$3" want_progress="$4" absent="$5" line plan progress
    line="$(hm resolve-active-plan --project-root "$proj" --plan verify-routing --with-tracker)"
    plan="$(printf '%s' "$line" | cut -f1)"; progress="$(printf '%s' "$line" | cut -f2)"
    assert_equals "$label: the plan resolves to $want_plan" "$plan" "$want_plan"
    assert_equals "$label: the progress log resolves beside it" "$progress" "$want_progress"
    # /plan writes the plan and opens its log; a task directory with no plan in
    # it is not a task, and the resolver would place a new one past it.
    mkdir -p "$(dirname "$plan")" "$(dirname "$progress")"
    printf '%s' "$PLAN_BODY" > "$plan"
    printf '%s\n' "2026-09-22 /plan — seeded" > "$progress"
    printf '%s\n' "2026-09-22 /work — step one" | hm append-progress --project-root "$proj" \
      --plan verify-routing >/dev/null
    assert_contains "$label: the kernel's append lands in the resolved log" \
      "$(cat "$progress" 2>/dev/null)" "/work — step one"
    assert_absent "$label: nothing lands at $absent" "$absent"
  }

  # ── A. vault backend: every plan is a task in the project's vault directory ─
  V_VAULT="$SCRATCH/vault"; mkdir -p "$V_VAULT/desk/projects/$SLUG"
  V_PROJ="$SCRATCH/proj-vault"; seed_project "$V_PROJ"
  MODE_ENV=("MEMORY_ROOT=$V_VAULT")
  V_TASK="$V_VAULT/desk/projects/$SLUG/tasks/001-verify-routing"
  route "A. vault backend" "$V_PROJ" "$V_TASK/plan.md" "$V_TASK/progress.md" \
    "$V_PROJ/.harness/progress-verify-routing.md"
  BARE_RC=0; hm resolve-active-plan --project-root "$V_PROJ" >/dev/null 2>&1 || BARE_RC=$?
  assert_equals "A. vault backend: a bare call names no plan (exit 4)" "$BARE_RC" "4"

  # ── A2. the vault-root projects/ generation (filing-v2 2b): a project that
  #        already lives beside the memory root resolves there, through a
  #        sibling-rooted backend, and its tasks sit in its own directory ─────
  R_ROOT="$SCRATCH/Vault"; R_VAULT="$R_ROOT/agent"; mkdir -p "$R_VAULT/memory" "$R_ROOT/projects/$SLUG" "$R_ROOT/.obsidian"
  R_PROJ="$SCRATCH/proj-root"; seed_project "$R_PROJ"
  MODE_ENV=("MEMORY_ROOT=$R_VAULT")
  R_TASK="$R_ROOT/projects/$SLUG/tasks/001-verify-routing"
  route "A2. root generation" "$R_PROJ" "$R_TASK/plan.md" "$R_TASK/progress.md" \
    "$R_VAULT/desk/projects/$SLUG/tasks"
  MODE_ENV=("MEMORY_ROOT=$V_VAULT")

  # ── B. .project-mode=local opts out even with a vault configured ───────────
  L_PROJ="$SCRATCH/proj-local-override"; seed_project "$L_PROJ"
  echo "local" > "$L_PROJ/.harness/.project-mode"
  route "B. .project-mode=local" "$L_PROJ" "$L_PROJ/.harness/PLAN-verify-routing.md" \
    "$L_PROJ/.harness/progress-verify-routing.md" "$V_VAULT/desk/projects/$SLUG/tasks/002-verify-routing"

  # ── C. no backend configured at all → device-local default ─────────────────
  N_PROJ="$SCRATCH/proj-none"; seed_project "$N_PROJ"
  MODE_ENV=()
  route "C. no backend configured" "$N_PROJ" "$N_PROJ/.harness/PLAN-verify-routing.md" \
    "$N_PROJ/.harness/progress-verify-routing.md" "$N_PROJ/tasks"
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
hm_broken() { env -u MEMORY_ROOT -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$D_PREFIX" \
  HARNESS_MEMORY_TOOLKIT_PATH="$S" OBSIDIAN_VAULT_SCRIPTS="$SHIM" "$PY" "$HM" "$@"; }
D_OUT="$(hm_broken resolve-active-plan --project-root "$D_PROJ" --plan verify-routing 2>&1)"
D_RC=$?
assert_equals   "D. never-demote: resolve-active-plan exits non-zero on broken vault + explicit backend=vault" \
  "$([ "$D_RC" -ne 0 ] && echo yes || echo no)" "yes"
assert_contains "D. never-demote: fail-loud message names the never-demote invariant" \
  "$D_OUT" "never silently demoting"
assert_equals   "D. never-demote: no device-local plan path is ever answered" \
  "$(printf '%s' "$D_OUT" | grep -c "$D_PROJ/.harness/PLAN")" "0"
printf '# seeded\n' > "$D_PROJ/.harness/progress-verify-routing.md"
D_APPEND_RC=0
printf 'x\n' | hm_broken append-progress --project-root "$D_PROJ" --plan verify-routing \
  >/dev/null 2>&1 || D_APPEND_RC=$?
assert_equals   "D. never-demote: append-progress exits non-zero too, and writes nothing locally" \
  "$([ "$D_APPEND_RC" -ne 0 ] && echo yes || echo no)$(cat "$D_PROJ/.harness/progress-verify-routing.md")" \
  "yes# seeded"


if [ "$FAULT" = "1" ]; then
  # ── fixture-validation: the pre-fix bare-except shape really would demote ──
  # Reproduces agentmEngine#1's exact defect (a local shadow — never patches
  # scripts/harness_memory.py) under the SAME broken-vault + explicit-backend
  # scratch scenario as D, to prove this fixture is a faithful regression case
  # for the historical bug (not just an arbitrary broken-config scenario).
  OLD_BUG_OUT="$(AGENTM_INSTALL_PREFIX="$D_PREFIX" env -u MEMORY_ROOT -u MEMORY_VAULT_PATH "$PY" - "$REPO/scripts" "$D_PROJ" <<'PYEOF'
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
