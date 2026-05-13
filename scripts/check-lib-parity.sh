#!/usr/bin/env bash
# check-lib-parity.sh — verify lib/install/ matches its committed checksums.
#
# Self-consistency check: recomputes SHA-256 of every file under lib/install/
# (excluding .checksums.txt itself) and asserts the result matches the
# committed .checksums.txt. Fails non-zero on any drift.
#
# Cross-repo byte-identity verification (agentic-harness ↔ agent-toolkit) is
# `scripts/sync-lib.sh --verify`. This script only checks the local repo.
#
# Usage:
#   bash scripts/check-lib-parity.sh
#
# Exit:
#   0  lib/install/ matches .checksums.txt
#   1  drift detected
#   2  .checksums.txt or lib/install/ missing

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_DIR="$REPO_ROOT/lib/install"
CHECKSUMS="$LIB_DIR/.checksums.txt"

if [[ ! -d "$LIB_DIR" ]]; then
    echo "check-lib-parity: $LIB_DIR does not exist" >&2
    exit 2
fi
if [[ ! -f "$CHECKSUMS" ]]; then
    echo "check-lib-parity: $CHECKSUMS missing — run 'bash scripts/sync-lib.sh' to generate" >&2
    exit 2
fi

# SHA-256 tool: prefer sha256sum (Linux/coreutils, Git Bash on Windows),
# fall back to shasum -a 256 (macOS/BSD).
if command -v sha256sum >/dev/null 2>&1; then
    SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA_CMD="shasum -a 256"
else
    echo "check-lib-parity: no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 2
fi

# Recompute, comparing each line against the committed file.
# LC_ALL=C forces byte-order sort for deterministic line order across
# platforms — macOS's default locale uses case-insensitive collation,
# producing different ordering than CI Linux (C locale). Same fix in
# sync-lib.sh.
RECOMPUTED=$(cd "$LIB_DIR" && find . -type f -not -name '.checksums.txt' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 $SHA_CMD \
    | sed 's| [ *]\./|  |')

COMMITTED=$(cat "$CHECKSUMS")

if [[ "$RECOMPUTED" == "$COMMITTED" ]]; then
    file_count=$(echo "$RECOMPUTED" | wc -l | tr -d ' ')
    echo "check-lib-parity: clean ($file_count file(s) under lib/install/ match .checksums.txt)"
    exit 0
fi

echo "check-lib-parity: DRIFT detected between lib/install/ and lib/install/.checksums.txt" >&2
echo "" >&2
echo "--- committed ---" >&2
echo "$COMMITTED" >&2
echo "" >&2
echo "--- recomputed ---" >&2
echo "$RECOMPUTED" >&2
echo "" >&2
echo "  Run 'bash scripts/sync-lib.sh' to regenerate checksums (and propagate to the sibling repo)." >&2
exit 1
