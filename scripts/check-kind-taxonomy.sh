#!/usr/bin/env bash
# check-kind-taxonomy.sh — V6-15 advisory kind-taxonomy audit (check-all.sh
# task 4, PLAN-v6-15-v6-18-typed-object-moc).
#
# Report-only, never a failing gate: the real vault's kind: taxonomy has
# genuine, known data-quality problems (near-duplicate values, a handful of
# malformed entries) that a hard-fail gate would block unrelated work on —
# the plan's own Risks section names this explicitly. This script runs
# kind_registry.py's audit() against $MEMORY_VAULT_PATH when set, prints the
# report, and always exits 0. Graceful-skip (also exit 0) when
# MEMORY_VAULT_PATH is unset or doesn't resolve to a real directory, matching
# the pattern other vault-dependent checks in this repo already use.
#
# Usage:
#   bash scripts/check-kind-taxonomy.sh
#
# Exit: always 0 (report-only by design — see above).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

if [[ -z "${MEMORY_VAULT_PATH:-}" ]] || [[ ! -d "${MEMORY_VAULT_PATH}" ]]; then
    echo "check-kind-taxonomy: MEMORY_VAULT_PATH unset or not a directory — skipping (report-only, no block)"
    exit 0
fi

"$PY" "$REPO_ROOT/harness/skills/memory/scripts/kind_registry.py" audit "$MEMORY_VAULT_PATH"
exit 0
