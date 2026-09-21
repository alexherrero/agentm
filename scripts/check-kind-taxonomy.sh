#!/usr/bin/env bash
# check-kind-taxonomy.sh — V6-15 advisory kind-taxonomy audit (check-all.sh
# task 4, PLAN-v6-15-v6-18-typed-object-moc).
#
# Report-only, never a failing gate: the real vault's kind: taxonomy has
# genuine, known data-quality problems (near-duplicate values, a handful of
# malformed entries) that a hard-fail gate would block unrelated work on —
# the plan's own Risks section names this explicitly. This script prints
# kind_registry.py's audit() report over the corpus the vocabulary gate
# resolves — $MEMORY_ROOT (or its deprecated alias $MEMORY_VAULT_PATH) when
# set, else the configured vault — and always exits 0. It asks
# check-vocabulary-membership.py --report for it, so the advisory report and
# the enforcing gate read the same notes. Graceful-skip (also exit 0) when
# nothing resolves, which is every CI runner. Until 2026-09-20 it read the
# export alone, and check-all.sh exports neither name, so the battery's run
# always skipped.
#
# Usage:
#   bash scripts/check-kind-taxonomy.sh
#
# Exit: always 0 (report-only by design — see above).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

echo "check-kind-taxonomy: the vocabulary audit's full report (report-only, never blocks)"
"$PY" "$REPO_ROOT/scripts/check-vocabulary-membership.py" --report
exit 0
