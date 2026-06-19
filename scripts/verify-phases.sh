#!/usr/bin/env bash
# verify-phases.sh — end-to-end phase-lifecycle integration check, run in BOTH
# state modes (vault-resident AND repo-local), proving #44's single-repo mode is
# first-class (Hardening I #45 task 5).
#
# Drives the DETERMINISTIC, non-LLM seams of a /setup → /plan → /work → /release
# lifecycle against a throwaway fixture project: project registration + the
# enablement block, recall graceful-skip, state read/write round-trips (PLAN.md,
# features.json, progress.md appends), and the post-phase dispatch plumbing
# (post-work / post-release, dry-run). It does NOT — and cannot — run the
# agent-driven reasoning of those phases; it tests exactly the plumbing that
# regresses silently (state I/O + dispatch + where writes LAND per mode), which
# is what a single-repo-mode regression breaks.
#
# Runs the REAL `harness_memory.py` / `project_config.py` CLIs against a `mktemp`
# scratch — never a real vault, never the network, never a sub-agent dispatch.
# The whole suite runs TWICE:
#   • vault pass  — MEMORY_VAULT_PATH set; state lands <vault>/projects/<slug>/_harness/
#   • local pass  — device state_mode:local, NO vault; state lands <repo>/.harness/
# so a write that lands in the wrong place (or a vault-assumption that breaks
# without a vault) fails loudly in one of the two passes.
#
# Usage:   bash scripts/verify-phases.sh
# Exit:    0 iff every check passes (CI / integration-test friendly).
#
# Negative check (task-5 verification): VERIFY_PHASES_FAULT=drop-plan-write skips
# the PLAN.md write so the downstream round-trip assertions MUST fail — proving a
# broken state-write path is caught, not silently passed. CI runs WITHOUT the
# fault. See the `write_state` helper.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"   # memory toolkit scripts (post-phase dispatch)
HM="$REPO/scripts/harness_memory.py"      # harness↔memory bridge
PC="$REPO/scripts/project_config.py"      # detect + register

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-phases: $PY not found" >&2; exit 2; }

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
# assert_exists <desc> <path>
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}
# assert_absent <desc> <path>  (passes iff path does NOT exist)
assert_absent() {
  if [ -e "$2" ]; then fail "$1" "did not expect path: $2"; else pass "$1"; fi
}

# ── scratch root (isolated; auto-removed) ───────────────────────────────────
SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
echo "verify-phases: scratch root = $SCRATCH"

SLUG="phasedemo"
PLAN_BODY=$'# Plan: fixture\n\n### 1. Task one\n- **Status:** [ ]\n'
FEATURES_BODY='[{"name": "fixture-feature", "passes": false}]'

# MODE_ENV is set per-pass; the helpers below run the CLIs under it. `-u` of BOTH
# vault + prefix vars keeps each pass hermetic (no leak from the other mode or the
# caller's shell).
MODE_ENV=()
hm()  { env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX "${MODE_ENV[@]+"${MODE_ENV[@]}"}" "$PY" "$HM" "$@"; }
pcli(){ env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX "${MODE_ENV[@]+"${MODE_ENV[@]}"}" PYTHONPATH="$REPO/scripts" "$PY" "$PC" "$@"; }

# write_state <proj> <file>  (content on stdin) — the state-write seam, with the
# fault hook for the negative check.
write_state() {
  if [ "${VERIFY_PHASES_FAULT:-}" = "drop-plan-write" ] && [ "$2" = "PLAN.md" ]; then
    cat >/dev/null   # consume stdin, write NOTHING → downstream asserts fail
    return 0
  fi
  hm write-state --project-root "$1" "$2" >/dev/null
}

# run_lifecycle <label> <proj_root> <expect_state_dir> <vault_mode:0|1>
run_lifecycle() {
  local label="$1" proj="$2" expect="$3" vault_mode="$4"

  # ── /setup seam: register the project + recall (graceful) ─────────────────
  local reg rc
  reg="$(pcli register "$proj" 2>&1)"; rc=$?
  assert_equals  "$label setup: register exits 0"                  "$rc" "0"
  assert_contains "$label setup: register emits the project type"   "$reg" '"type": "coding"'
  # The enablement block must land in this mode's state home (vault vs repo-local).
  assert_exists  "$label setup: enablement block at $expect/project.json" "$expect/project.json"
  if [ -f "$expect/project.json" ]; then
    assert_contains "$label setup: project.json carries the skills block" \
      "$(cat "$expect/project.json")" '"skills"'
  fi
  # recall is invoked by every phase; with an empty/absent vault it graceful-skips.
  hm recall --phase setup --project "$SLUG" >/dev/null 2>&1
  assert_equals  "$label setup: recall graceful-skip (exit 0)"     "$?" "0"

  # ── /plan seam: write PLAN.md + features.json; round-trip; correct location ─
  printf '%s' "$PLAN_BODY" | write_state "$proj" PLAN.md
  assert_exists  "$label plan: PLAN.md lands in this mode's home"  "$expect/PLAN.md"
  assert_contains "$label plan: PLAN.md round-trips" \
    "$(hm read-state --project-root "$proj" PLAN.md 2>/dev/null)" "Task one"
  printf '%s' "$FEATURES_BODY" | write_state "$proj" features.json
  assert_contains "$label plan: features.json round-trips" \
    "$(hm read-state --project-root "$proj" features.json 2>/dev/null)" '"passes": false'

  # ── /work seam: progress.md append round-trip (read → append → write) ──────
  printf '%s\n' "2026-01-01 /plan — seeded" | write_state "$proj" progress.md
  local prev
  prev="$(hm read-state --project-root "$proj" progress.md 2>/dev/null)"
  printf '%s\n%s\n' "$prev" "2026-01-02 /work — task one done" | write_state "$proj" progress.md
  local prog
  prog="$(hm read-state --project-root "$proj" progress.md 2>/dev/null)"
  assert_contains "$label work: progress.md append landed"        "$prog" "task one done"
  assert_contains "$label work: progress.md preserved prior line" "$prog" "/plan — seeded"

  # ── post-phase dispatch plumbing (dry-run, non-blocking) ──────────────────
  # vault mode → renders the real dry-run plan; local mode (no vault) → the
  # dispatcher graceful-skips (exit 0, empty) — proving it degrades cleanly.
  local pw pr
  pw="$(hm phase-dispatch post-work --project-root "$proj" --dry-run 2>/dev/null)"; rc=$?
  assert_equals  "$label work: post-work dispatch non-blocking (exit 0)" "$rc" "0"
  pr="$(hm phase-dispatch post-release --project-root "$proj" --dry-run 2>/dev/null)"; rc=$?
  assert_equals  "$label release: post-release dispatch non-blocking (exit 0)" "$rc" "0"
  if [ "$vault_mode" = "1" ]; then
    assert_contains "$label work: post-work renders a dry-run plan"  "$pw" '"status"'
    assert_contains "$label release: post-release runs index-skills"    "$pr" 'index_skills.py'
    assert_contains "$label release: post-release runs discover-skills" "$pr" 'discover_skills.py'
  else
    assert_equals  "$label work: post-work graceful-skips (no vault, empty)"  "$pw" ""
    assert_equals  "$label release: post-release graceful-skips (no vault)"   "$pr" ""
  fi
}

# ── PASS 1: vault-resident ───────────────────────────────────────────────────
echo "verify-phases: ── pass 1/2 — vault-resident ──"
V_VAULT="$SCRATCH/vault"; mkdir -p "$V_VAULT/projects"
V_PROJ="$SCRATCH/vault-proj"; mkdir -p "$V_PROJ/.harness"
printf '{"vault_project": "%s"}\n' "$SLUG" > "$V_PROJ/.harness/project.json"
# Toolkit path lets the post-release dispatch resolve orchestration_phase.py.
# OBSIDIAN_VAULT_SCRIPTS: point to the kernel scripts dir so backend_selection
# can load the kernel VaultBackend when the crickets obsidian-vault plugin is
# not installed (CI). The kernel built-in has the same interface and writes the
# same registry format; this is a CI-isolation shim, not a behavior change.
MODE_ENV=("MEMORY_VAULT_PATH=$V_VAULT" "HARNESS_MEMORY_TOOLKIT_PATH=$S" "OBSIDIAN_VAULT_SCRIPTS=$REPO/scripts")
run_lifecycle "[vault]" "$V_PROJ" "$V_PROJ/.harness" 1
# The vault repo_registry seam fired (cross-device index).
assert_exists "[vault] setup: repo_registry index written" "$V_VAULT/_meta/repos.json"
# V5-3: state is device-local; vault _harness/ must NOT be created by the kernel.
assert_absent "[vault] isolation: vault _harness/ not written by kernel (V5-3)" \
  "$V_VAULT/projects/$SLUG/_harness/PLAN.md"

# ── PASS 2: repo-local (device state_mode:local, NO vault) ───────────────────
echo "verify-phases: ── pass 2/2 — repo-local (vault-less) ──"
L_PREFIX="$SCRATCH/prefix"; mkdir -p "$L_PREFIX"
printf '{"schema_version": 2, "mode": "release", "state_mode": "local"}\n' \
  > "$L_PREFIX/.agentm-config.json"
L_PROJ="$SCRATCH/local-proj"; mkdir -p "$L_PROJ/.harness"
printf '{"vault_project": "%s"}\n' "$SLUG" > "$L_PROJ/.harness/project.json"
MODE_ENV=("AGENTM_INSTALL_PREFIX=$L_PREFIX")
run_lifecycle "[local]" "$L_PROJ" "$L_PROJ/.harness" 0
# Local mode (no vault) must not invent a vault repo_registry — neither under the
# device prefix nor the project tree (the register() guard skips it when vault is None).
assert_absent "[local] isolation: no registry under device prefix" "$L_PREFIX/_meta/repos.json"
assert_absent "[local] isolation: no registry under project tree"  "$L_PROJ/_meta/repos.json"

# ── phase-dispatch bridge: session-marker scenarios ─────────────────────────
# Verifies the concurrency-safe session-discovery that post-work relies on.
# Relocated from verify-v4.sh (V5-5 task 4 fracture) — Developer-half owner.
echo "verify-phases: ── bridge: session-marker scenarios ──"
SM_PROJ="$SCRATCH/sm-proj"; mkdir -p "$SM_PROJ/.harness"
SM_VAULT="$SCRATCH/sm-vault"; mkdir -p "$SM_VAULT"
sm_hm() { env -u AGENTM_INSTALL_PREFIX MEMORY_VAULT_PATH="$SM_VAULT" HARNESS_MEMORY_TOOLKIT_PATH="$S" "$PY" "$HM" "$@"; }

PW_SM0="$(sm_hm phase-dispatch post-work --project-root "$SM_PROJ" --dry-run 2>/dev/null)"
assert_contains "bridge: post-work no marker → no-session"          "$PW_SM0" '"status": "no-session"'
printf 'session_id: s\ntranscript: /tmp/t.jsonl\n' > "$SM_PROJ/.harness/session-id-s.start"
PW_SM1="$(sm_hm phase-dispatch post-work --project-root "$SM_PROJ" --dry-run 2>/dev/null)"
assert_contains "bridge: post-work single marker → dry-run plan"    "$PW_SM1" '"status": "dry-run"'
assert_contains "bridge: post-work reflect uses --route"            "$PW_SM1" '--route'
printf 'session_id: s2\ntranscript: /tmp/t2.jsonl\n' > "$SM_PROJ/.harness/session-id-s2.start"
PW_SM2="$(sm_hm phase-dispatch post-work --project-root "$SM_PROJ" --dry-run 2>/dev/null)"
assert_contains "bridge: 2 markers → ambiguous-session"             "$PW_SM2" '"status": "ambiguous-session"'
PR_SM="$(sm_hm phase-dispatch post-release --project-root "$SM_PROJ" --dry-run 2>/dev/null)"
assert_contains "bridge: post-release runs discover-skills"         "$PR_SM"  'discover_skills.py'

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-phases: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
