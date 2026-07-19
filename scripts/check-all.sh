#!/usr/bin/env bash
# check-all.sh — the standard local gate battery. Run before every commit.
#
# One command that runs the deterministic checks CI runs (unit tests + the
# check-* gates) plus the verify-v4 push-surface integration test. Each gate is
# independent — a failure doesn't abort the rest; the script prints a PASS/FAIL
# table and exits non-zero iff any gate failed.
#
# Usage:   bash scripts/check-all.sh
# Exit:    0 iff every gate passes.
#
# CI additionally runs the heavier smoke-install + gitleaks on every push; those
# are intentionally NOT in this fast local battery (slow / external tooling).
# Grow this battery as the project grows: add a `gate "<name>" <command...>` line.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "check-all: $PY not found" >&2; exit 2; }

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

PASS=0; FAIL=0
RESULTS=()

# gate "<name>" <command...>  — run a gate; capture output; record pass/fail.
gate() {
  local name="$1"; shift
  printf '  … %s\n' "$name" >&2
  if "$@" >"$LOG" 2>&1; then
    RESULTS+=("  PASS  $name"); PASS=$((PASS+1))
  else
    RESULTS+=("  FAIL  $name"$'\n'"$(tail -8 "$LOG" | sed 's/^/          | /')"); FAIL=$((FAIL+1))
  fi
}

# gate_skip "<name>" "<reason>"  — record a named gate as skipped (not a
# failure) when its prerequisite tool isn't on this machine. Keeps the gate
# named/countable in the printed table without forcing every machine to have
# every optional tool installed.
gate_skip() {
  RESULTS+=("  SKIP  $1 — $2")
}

echo "check-all: running the local gate battery…" >&2

gate "unit tests (scripts/test_*.py)"          bash -c "cd scripts && $PY -m unittest discover -p 'test_*.py'"
gate "check-syntax (bash -n every .sh)"        bash scripts/check-syntax.sh
if command -v pwsh >/dev/null 2>&1; then
  gate "check-syntax.ps1 (PowerShell AST parse every .ps1)" pwsh -NoProfile -File scripts/check-syntax.ps1
else
  gate_skip "check-syntax.ps1 (PowerShell AST parse every .ps1)" "pwsh not on this machine (CI runs it on all 3 OS matrices)"
fi
gate "check-references"                        "$PY" scripts/check-references.py
gate "validate-adapters"                       "$PY" scripts/validate-adapters.py
gate "check-parity (adapter sets)"             bash scripts/check-parity.sh
gate "check-vendored-parity (lib + vault-lock + wiki-publish-transform + storage-seam + hook-config + workflow)" bash scripts/check-vendored-parity.sh
gate "check-multi-plan-naming (named-plan contract)" bash scripts/check-multi-plan-naming.sh
gate "check-worktree-slug (slug == origin basename)" bash scripts/check-worktree-slug.sh
gate "check-no-auto-worktree (no agentm auto-spawn)" bash scripts/check-no-auto-worktree.sh
gate "check-one-way-imports (process-seam + LC-8 back-edges + opinion/capability resolvers, never import plugin/process code)" "$PY" scripts/check-one-way-imports.py
gate "check-storage-seam-no-path-leak (no Path crosses the seam)" "$PY" scripts/check-storage-seam-no-path-leak.py
gate "check-opinion-honesty (no orphan Opinion references)" "$PY" scripts/check-opinion-honesty.py
gate "check-personas (requires ⊆ substrate + no-always-load)" "$PY" scripts/check-personas.py
gate "check-governs-index (governs:/area: overlap + unknown-area)" "$PY" scripts/check-governs-index.py
gate "check-no-hardcoded-vault-path (no absolute vault literals)" "$PY" scripts/check-no-hardcoded-vault-path.py
gate "check-no-pii (--all)"                    bash scripts/check-no-pii.sh --all
gate "check-wiki (--strict)"                   "$PY" scripts/check-wiki.py --strict
gate "check-slop (--strict)"                    "$PY" scripts/check-slop.py --strict --wiki-root wiki wiki
gate "verify-v4 (push-surface integration)"    bash scripts/verify-v4.sh
gate "verify-orchestration-briefing (PM-half · briefing + nudge signals)" bash scripts/verify-orchestration-briefing.sh
gate "verify-hook-resolution (dual-key vault_path read, 4 hooks)" bash scripts/verify-hook-resolution.sh
gate "verify-state-routing (backend x project-mode matrix, never-demote)" bash scripts/verify-state-routing.sh
gate "verify-vec-index (drain pipeline e2e, freshness invariant)" bash scripts/verify-vec-index.sh
gate "verify-reflection (tri-lane routing, machine-source filter)" bash scripts/verify-reflection.sh
gate "verify-dreaming (stage->confirm->revert-log e2e, no-silent-apply-on-timeout)" bash scripts/verify-dreaming.sh
gate "verify-auto-org-meters (connectivity + browse-surface e2e)" bash scripts/verify-auto-org-meters.sh
gate "verify-mcp-surface (append/search/forget round-trip, dead-surface fate)" "$PY" scripts/verify-mcp-surface.py
gate "health-score-determinism (scorecard byte-identical across two runs)" "$PY" scripts/health/health_score.py --check-determinism --path scripts/health/fixtures/sample-records.jsonl
gate "validate-audit-coverage (5 in-scope mythos-readiness blockers detectable)" bash scripts/health/validate-audit-coverage.sh
gate "run-ablation-baseline (mechanical uplift, on/off per subsystem)" bash scripts/health/run-ablation-baseline.sh
gate "verify-phases (lifecycle e2e · both modes)" bash scripts/verify-phases.sh
gate "verify-memory-roundtrip (engine e2e)"    bash scripts/verify-memory-roundtrip.sh
gate "check-kind-taxonomy (advisory, report-only — V6-15)" bash scripts/check-kind-taxonomy.sh

echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "check-all: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
