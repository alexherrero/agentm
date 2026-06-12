#!/usr/bin/env bash
# check-multi-plan-naming.sh — lock the named-plan naming contract (V5-10 part 1).
#
# Two assertions guard the substrate that lets N workers each own a distinct
# PLAN-<name>.md without colliding on harness state:
#
#   1. The resolver exposes the named-plan surface. scripts/harness_memory.py must
#      define resolve_active_plan() (the (plan, progress) binding the crickets
#      phase loop consumes) and harness_state_dir() (the directory enumerator
#      queue_status_lite + the session-start hooks use). A refactor that drops
#      either silently breaks named-plan binding — this fails loudly instead.
#
#   2. No curated harness/*.md doc hard-asserts a SINGLETON plan. Task 4 rewrote
#      the "the PLAN.md" / "PLAN.md's" singleton framings to acknowledge named
#      plans; this gate stops a later edit from silently re-introducing the
#      single-plan assumption. The deny-pattern is deliberately NARROW — only the
#      definite-article ("the `PLAN.md`") and possessive ("`PLAN.md`'s") forms —
#      and PERMITS every legitimate mention: a named `PLAN-<name>.md`, a `PLAN*.md`
#      glob, a `<slug>.PLAN.md` queued file, the `vault-state-path PLAN.md` CLI
#      example, and `PLAN.archive.*` names. (Risk #3: a broad pattern would
#      false-positive on design/SKILL.md's many legitimate named-plan mentions.)
#
# Usage:  bash scripts/check-multi-plan-naming.sh [--root DIR]
#   --root DIR   scan DIR instead of the repo root — the negative test points the
#                gate at a malformed fixture tree.
# Exit:   0  the contract holds
#         1  a singleton assertion, or a missing resolver surface, was found
#         2  setup error (curated file or harness_memory.py missing)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$REPO_ROOT"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --root=*) ROOT="${1#--root=}"; shift ;;
    *) echo "check-multi-plan-naming: unknown arg: $1" >&2; exit 2 ;;
  esac
done

fail=0

# --- assertion 1: resolver exposes the named-plan substrate surface ---------
HM="$ROOT/scripts/harness_memory.py"
if [ ! -f "$HM" ]; then
  echo "check-multi-plan-naming: missing $HM" >&2
  exit 2
fi
for sym in resolve_active_plan harness_state_dir; do
  if ! grep -qE "^def ${sym}\b" "$HM"; then
    echo "check-multi-plan-naming: harness_memory.py lost the named-plan surface 'def ${sym}(…)'" >&2
    fail=1
  fi
done

# --- assertion 2: no singleton assertion in the curated prose docs ----------
# The prose surfaces task 4 migrated, plus design/SKILL.md (already named-plan
# aware — audited, zero hits — kept here as a regression guard).
CURATED=(
  harness/principles.md
  harness/hooks.md
  harness/verification.md
  harness/documentation.md
  harness/skills/doctor.md
  harness/skills/memory/SKILL.md
  harness/skills/design/SKILL.md
)

# Deny: definite-article ("the PLAN.md") or possessive ("PLAN.md's") singleton.
DENY="the +\`?PLAN\.md|PLAN\.md\`?['’]s"
# Permit: lines that mention PLAN.md WITHOUT asserting singularity.
PERMIT="PLAN-|PLAN\*|\.PLAN\.md|vault-state-path PLAN\.md|PLAN\.archive"

for rel in "${CURATED[@]}"; do
  f="$ROOT/$rel"
  if [ ! -f "$f" ]; then
    echo "check-multi-plan-naming: curated file missing: $f" >&2
    exit 2
  fi
  hits="$(grep -nE "$DENY" "$f" | grep -vE "$PERMIT" || true)"
  if [ -n "$hits" ]; then
    echo "check-multi-plan-naming: singleton plan assertion in $rel —" >&2
    printf '%s\n' "$hits" | sed 's/^/    /' >&2
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "" >&2
  echo "  Rewrite 'the PLAN.md' / \"PLAN.md's\" to acknowledge named plans, e.g." >&2
  echo "  'a plan file (\`PLAN.md\` or \`PLAN-<name>.md\`)'. See wiki/explanation/Named-Plans.md." >&2
  exit 1
fi

echo "check-multi-plan-naming: clean — resolver surface present; no singleton assertion across ${#CURATED[@]} curated docs."
exit 0
