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

echo "check-all: running the local gate battery…" >&2

gate "unit tests (scripts/test_*.py)"          bash -c "cd scripts && $PY -m unittest discover -p 'test_*.py'"
gate "check-syntax (bash -n every .sh)"        bash scripts/check-syntax.sh
gate "check-references"                        "$PY" scripts/check-references.py
gate "validate-adapters"                       "$PY" scripts/validate-adapters.py
gate "check-parity (adapter sets)"             bash scripts/check-parity.sh
gate "check-lib-parity (lib/install checksums)" bash scripts/check-lib-parity.sh
gate "check-vault-lock-parity (vendored vault_lock)" bash scripts/check-vault-lock-parity.sh
gate "check-workflow-parity (templated workflows byte-identical)" bash scripts/check-workflow-parity.sh
gate "check-multi-plan-naming (named-plan contract)" bash scripts/check-multi-plan-naming.sh
gate "check-worktree-slug (slug == origin basename)" bash scripts/check-worktree-slug.sh
gate "check-no-auto-worktree (no agentm auto-spawn)" bash scripts/check-no-auto-worktree.sh
gate "check-process-seam-import-direction (memory never imports the process)" bash scripts/check-process-seam-import-direction.sh
gate "check-storage-seam-no-path-leak (no Path crosses the seam)" "$PY" scripts/check-storage-seam-no-path-leak.py
gate "check-no-pii (--all)"                    bash scripts/check-no-pii.sh --all
gate "check-wiki (--strict)"                   "$PY" scripts/check-wiki.py --strict
gate "verify-v4 (push-surface integration)"    bash scripts/verify-v4.sh
gate "verify-phases (lifecycle e2e · both modes)" bash scripts/verify-phases.sh
gate "verify-memory-roundtrip (engine e2e)"    bash scripts/verify-memory-roundtrip.sh

echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "check-all: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
