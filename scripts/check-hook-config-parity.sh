#!/usr/bin/env bash
# check-hook-config-parity.sh — assert the four memory hooks' `_resolve_vault_path`
# implementations are byte-identical (R0.1 / agentmEngine#0).
#
# `_resolve_vault_path` is vendored into four hook scripts (env → .agentm-config.json
# plugin key → legacy flat key → none). They are four copies of the same logic, not
# four independent implementations — a fix or a bug applied to only one of them is
# drift. This gate extracts the function body from each hook and diffs it against the
# first hook's copy, byte-for-byte.
#
# Usage:  bash scripts/check-hook-config-parity.sh
# Exit:   0  all four copies are identical
#         1  drift detected (a copy differs — re-apply the fix to the odd one out)
#         2  a hook file or the function is missing

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOOKS=(
  "harness/hooks/memory-recall-session-start/memory-recall-session-start.sh"
  "harness/hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.sh"
  "harness/hooks/memory-reflect-stop/memory-reflect-stop.sh"
  "harness/hooks/memory-reflect-idle/memory-reflect-idle.sh"
)

# Extract the `_resolve_vault_path() { ... }` block verbatim (opening line
# through the first line that is exactly `}`).
_extract() {
  awk '/^_resolve_vault_path\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$1"
}

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

FIRST=""
for hook in "${HOOKS[@]}"; do
  path="$REPO_ROOT/$hook"
  if [[ ! -f "$path" ]]; then
    echo "check-hook-config-parity: missing $path" >&2
    exit 2
  fi
  out="$TMPDIR/$(basename "$hook").func"
  _extract "$path" > "$out"
  if [[ ! -s "$out" ]]; then
    echo "check-hook-config-parity: _resolve_vault_path not found in $path" >&2
    exit 2
  fi
  if [[ -z "$FIRST" ]]; then
    FIRST="$out"
    FIRST_HOOK="$hook"
  fi
done

DRIFT=0
for hook in "${HOOKS[@]}"; do
  out="$TMPDIR/$(basename "$hook").func"
  if ! diff -q "$FIRST" "$out" >/dev/null 2>&1; then
    echo "check-hook-config-parity: DRIFT — $hook's _resolve_vault_path differs from $FIRST_HOOK" >&2
    diff -u "$FIRST" "$out" >&2 || true
    DRIFT=1
  fi
done

if [[ "$DRIFT" -ne 0 ]]; then
  exit 1
fi

echo "check-hook-config-parity: clean (all four _resolve_vault_path copies are byte-identical)"
exit 0
