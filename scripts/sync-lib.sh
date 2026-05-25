#!/usr/bin/env bash
# sync-lib.sh — keep agentm and crickets's lib/install/ byte-identical.
#
# Treats agentm as the canonical source. Copies lib/install/ verbatim
# into ../crickets/lib/install/, regenerates .checksums.txt in both
# repos, and leaves the changes staged for the user to commit.
#
# Usage:
#   bash scripts/sync-lib.sh             # canonical → sibling
#   bash scripts/sync-lib.sh --verify    # only check; no copy
#
# Exit:
#   0  in-sync (or sync succeeded)
#   1  drift detected (in --verify) or sync failed

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLKIT_ROOT="$(cd "$HARNESS_ROOT/../crickets" 2>/dev/null && pwd || true)"

MODE="sync"
if [[ "${1:-}" == "--verify" ]]; then
    MODE="verify"
fi

if [[ -z "$TOOLKIT_ROOT" ]]; then
    echo "sync-lib: cannot locate ../crickets/ relative to $HARNESS_ROOT" >&2
    echo "  Expected sibling layout: ../crickets/lib/install/" >&2
    exit 1
fi

CANONICAL="$HARNESS_ROOT/lib/install"
MIRROR="$TOOLKIT_ROOT/lib/install"

if [[ ! -d "$CANONICAL" ]]; then
    echo "sync-lib: canonical $CANONICAL does not exist" >&2
    exit 1
fi

# ── compute checksums (excludes .checksums.txt itself) ────────────────────
# Sorted output for deterministic diffs across machines.
# LC_ALL=C forces byte-order sort (case-sensitive); without it, macOS Mac's
# default locale uses case-insensitive collation, producing different line
# order than Linux (which often defaults to C locale in CI). Same fix in
# check-lib-parity.sh.
#
# SHA-256 tool: `shasum -a 256` on macOS/BSD; `sha256sum` on Linux/coreutils
# (and Git Bash on Windows, which doesn't ship shasum). Detect and use
# whichever is present.
if command -v sha256sum >/dev/null 2>&1; then
    _SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    _SHA_CMD="shasum -a 256"
else
    echo "sync-lib: no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 2
fi

compute_checksums() {
    local root="$1"
    # sha256sum output formats:
    #   text mode (Linux/Mac default): "<hash>  ./<file>"   (2 spaces)
    #   binary mode (Win Git Bash default): "<hash> *./<file>"  (space-asterisk)
    # Normalize both to "<hash>  <file>" (2 spaces, no leading ./)
    (cd "$root" && find . -type f -not -name '.checksums.txt' -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 $_SHA_CMD \
        | sed 's| [ *]\./|  |')
}

# ── sync mode: copy + regenerate ──────────────────────────────────────────
if [[ "$MODE" == "sync" ]]; then
    echo "==> syncing lib/install/ from agentm → crickets"

    # Ensure mirror dir exists; wipe its contents so deletions in canonical
    # propagate.
    mkdir -p "$MIRROR"
    rm -rf "$MIRROR"/*
    cp -R "$CANONICAL"/* "$MIRROR/"
    echo "    copied $(find "$CANONICAL" -type f -not -name '.checksums.txt' | wc -l | tr -d ' ') files"

    # Regenerate checksums in both
    compute_checksums "$CANONICAL" > "$CANONICAL/.checksums.txt"
    compute_checksums "$MIRROR"   > "$MIRROR/.checksums.txt"
    echo "    regenerated .checksums.txt in both repos"

    # Stage changes in crickets for user review (agentm changes
    # are already in the working tree, presumably staged by the user before
    # running this).
    (cd "$TOOLKIT_ROOT" && git add lib/install/)
    echo ""
    echo "sync-lib: complete."
    echo "  Review staged changes in both repos and commit with parallel"
    echo "  messages cross-referencing the other repo's commit SHA."
    exit 0
fi

# ── verify mode: byte-compare without copying ─────────────────────────────
if [[ "$MODE" == "verify" ]]; then
    echo "==> verifying lib/install/ byte-identity"

    if ! diff -qr "$CANONICAL" "$MIRROR" >/dev/null 2>&1; then
        echo "sync-lib: DRIFT detected between $CANONICAL and $MIRROR" >&2
        diff -qr "$CANONICAL" "$MIRROR" >&2 || true
        echo "" >&2
        echo "  Run 'bash scripts/sync-lib.sh' to re-sync." >&2
        exit 1
    fi
    echo "sync-lib: in-sync (verified by diff -qr)"
    exit 0
fi
