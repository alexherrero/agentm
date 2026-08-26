#!/usr/bin/env bash
# sync-lib.sh — regenerate agentm's lib/install/.checksums.txt.
#
# Since the crickets clean break (crickets v3.0 #40 part 5), agentm and
# crickets no longer share lib/install/: crickets ships NATIVE plugins and
# keeps no install primitives. This script is LOCAL-ONLY — it regenerates
# agentm's own lib/install/.checksums.txt (the manifest that
# check-vendored-parity.sh's `lib` mode verifies on every push). It does not
# copy into ../crickets/ (the cross-repo byte-sync coupling is gone).
#
# What lib/install/ still holds, after the per-project install was retired, is
# the Python install primitives the machine-wide path runs on —
# install_state.py, install_symlinks.py, install_copy.py. The bash/pwsh copy
# helpers (cp_user, cp_managed, ensure_boundary_src and friends) served only
# the retired --scope project path and are gone. The checksum manifest still
# earns its keep over what remains: those three files are what an install
# actually executes.
#
# Usage:
#   bash scripts/sync-lib.sh             # regenerate .checksums.txt
#   bash scripts/sync-lib.sh --verify    # check only; no write (exit 1 on drift)
#
# Exit:
#   0  in-sync (or regeneration succeeded)
#   1  drift detected (in --verify)
#   2  no SHA-256 tool

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="regen"
if [[ "${1:-}" == "--verify" ]]; then
    MODE="verify"
fi

CANONICAL="$HARNESS_ROOT/lib/install"

if [[ ! -d "$CANONICAL" ]]; then
    echo "sync-lib: $CANONICAL does not exist" >&2
    exit 1
fi

# ── SHA-256 tool detection ────────────────────────────────────────────────
# `sha256sum` on Linux/coreutils (and Git Bash on Windows); `shasum -a 256`
# on macOS/BSD. Detect whichever is present.
if command -v sha256sum >/dev/null 2>&1; then
    _SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    _SHA_CMD="shasum -a 256"
else
    echo "sync-lib: no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 2
fi

# Sorted, LC_ALL=C byte-order output for deterministic cross-machine diffs.
# LC_ALL=C forces case-sensitive byte ordering; without it macOS's default
# locale collates case-insensitively, producing different line order than
# Linux/CI. Same normalization as check-vendored-parity.sh's lib mode.
#
# sha256sum output formats:
#   text mode (Linux/Mac default): "<hash>  ./<file>"   (2 spaces)
#   binary mode (Win Git Bash):    "<hash> *./<file>"   (space-asterisk)
# Normalize both to "<hash>  <file>" (2 spaces, no leading ./).
compute_checksums() {
    local root="$1"
    (cd "$root" && find . -type f -not -name '.checksums.txt' -not -path './__pycache__/*' -not -path './python/__pycache__/*' -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 $_SHA_CMD \
        | sed 's| [ *]\./|  |')
}

if [[ "$MODE" == "verify" ]]; then
    echo "==> verifying lib/install/.checksums.txt is current"
    if ! diff -q <(compute_checksums "$CANONICAL") "$CANONICAL/.checksums.txt" >/dev/null 2>&1; then
        echo "sync-lib: DRIFT — lib/install/ no longer matches .checksums.txt" >&2
        echo "  Run 'bash scripts/sync-lib.sh' to regenerate." >&2
        exit 1
    fi
    echo "sync-lib: in-sync"
    exit 0
fi

# ── regen mode ────────────────────────────────────────────────────────────
compute_checksums "$CANONICAL" > "$CANONICAL/.checksums.txt"
echo "sync-lib: regenerated $CANONICAL/.checksums.txt"
echo "  Review the staged change and commit."
exit 0
