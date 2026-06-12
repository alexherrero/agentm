#!/usr/bin/env bash
# check-vault-lock-parity.sh — assert the two vault_lock.py copies are byte-identical.
#
# vault_lock.py (the V5-0 vault-write protocol — the advisory mutex + atomic
# writer + content-hash CAS) has TWO physical homes by necessity (DC-9):
#
#   scripts/vault_lock.py                          ← canonical home
#   harness/skills/memory/scripts/vault_lock.py    ← vendored sibling
#
# The /memory save+evolve scripts live in the skill dir and import the sibling
# (`from vault_lock import …`); top-level scripts/ is NOT on sys.path in a real
# install, so a cross-tree import would ImportError there. Vendoring a
# co-located byte-identical copy is the only mechanism that survives all three
# install scopes (matches the existing vec_index.py "duplicate to avoid
# cross-script import coupling" idiom + the lib/install byte-parity pattern).
#
# This gate enforces DC-4's "one *logical* library" as "byte-identical across
# its two physical homes" — the security-critical write primitive can never
# silently drift. Mirrors check-lib-parity.sh.
#
# Usage:  bash scripts/check-vault-lock-parity.sh
# Exit:   0  the two copies are sha256-identical
#         1  drift detected (re-vendor with the cp shown below)
#         2  a copy is missing / no SHA-256 tool

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANON="$REPO_ROOT/scripts/vault_lock.py"
VENDORED="$REPO_ROOT/harness/skills/memory/scripts/vault_lock.py"

for f in "$CANON" "$VENDORED"; do
    if [[ ! -f "$f" ]]; then
        echo "check-vault-lock-parity: missing $f" >&2
        exit 2
    fi
done

# SHA-256 tool: prefer sha256sum (Linux/coreutils, Git Bash on Windows),
# fall back to shasum -a 256 (macOS/BSD). Feed via stdin so the output is the
# bare hash with no path attached (path-independent comparison).
if command -v sha256sum >/dev/null 2>&1; then
    SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA_CMD="shasum -a 256"
else
    echo "check-vault-lock-parity: no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 2
fi

H_CANON="$($SHA_CMD < "$CANON" | awk '{print $1}')"
H_VENDORED="$($SHA_CMD < "$VENDORED" | awk '{print $1}')"

if [[ "$H_CANON" == "$H_VENDORED" ]]; then
    echo "check-vault-lock-parity: clean (both vault_lock.py copies are sha256-identical: ${H_CANON:0:12}…)"
    exit 0
fi

echo "check-vault-lock-parity: DRIFT — the two vault_lock.py copies differ" >&2
echo "  canonical $CANON: $H_CANON" >&2
echo "  vendored  $VENDORED: $H_VENDORED" >&2
echo "" >&2
echo "--- diff (canonical → vendored) ---" >&2
diff "$CANON" "$VENDORED" >&2 || true
echo "" >&2
echo "  Re-vendor with: cp scripts/vault_lock.py harness/skills/memory/scripts/vault_lock.py" >&2
exit 1
