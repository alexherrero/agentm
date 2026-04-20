#!/usr/bin/env bash
# check-syntax.sh — parse every .sh under the repo with `bash -n`.
# Catches syntax errors that `set -euo pipefail` would miss (dead branches,
# here-doc typos, arg-expansion bugs that don't fire in the golden path).
#
# Scope:
#   - install.sh (repo root)
#   - scripts/*.sh
#   - templates/**/*.sh
#   - adapters/**/*.sh  (none today, but future-proof)
#
# Skips .ps1 (that's check-syntax.ps1's job).
#
# Exits non-zero on first parse failure. Prints count on success.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HARNESS_ROOT"

count=0
fail=0
while IFS= read -r -d '' f; do
  if ! bash -n "$f" 2>&1; then
    echo "FAIL: bash -n $f" >&2
    fail=1
  fi
  count=$((count + 1))
done < <(find install.sh scripts templates adapters -type f -name '*.sh' -print0 2>/dev/null)

if [[ $fail -ne 0 ]]; then
  exit 1
fi

echo "check-syntax: $count .sh files parse cleanly."
