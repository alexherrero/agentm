#!/usr/bin/env bash
# check-storage-seam-vendor-parity.sh — assert the vendored storage-seam
# copies are byte-identical to their canonical originals.
#
# V5-14 (storage convergence) routes save.py + evolve.py through the seam's
# StorageBackend verbs instead of calling atomic_write directly. Those
# scripts live in harness/skills/memory/scripts/, which is NOT on sys.path
# in a real install (DC-9 — see check-vault-lock-parity.sh's own header for
# the full rationale) — so storage_seam.py + storage_device_local.py are
# vendored there too, alongside the existing vault_lock.py vendored copy.
# Two files, two canonical/vendored pairs:
#
#   scripts/storage_seam.py                          <-> harness/skills/memory/scripts/storage_seam.py
#   scripts/storage_device_local.py                  <-> harness/skills/memory/scripts/storage_device_local.py
#
# Mirrors check-vault-lock-parity.sh exactly (same SHA-256 tool fallback,
# same exit-code contract), just over two files instead of one.
#
# Usage:  bash scripts/check-storage-seam-vendor-parity.sh
# Exit:   0  every pair is sha256-identical
#         1  drift detected in at least one pair
#         2  a copy is missing / no SHA-256 tool

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PAIRS=(
    "scripts/storage_seam.py|harness/skills/memory/scripts/storage_seam.py"
    "scripts/storage_device_local.py|harness/skills/memory/scripts/storage_device_local.py"
)

if command -v sha256sum >/dev/null 2>&1; then
    SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA_CMD="shasum -a 256"
else
    echo "check-storage-seam-vendor-parity: no SHA-256 tool found (need sha256sum or shasum)" >&2
    exit 2
fi

FAILED=0
for pair in "${PAIRS[@]}"; do
    canon="$REPO_ROOT/${pair%%|*}"
    vendored="$REPO_ROOT/${pair##*|}"
    for f in "$canon" "$vendored"; do
        if [[ ! -f "$f" ]]; then
            echo "check-storage-seam-vendor-parity: missing $f" >&2
            exit 2
        fi
    done
    h_canon="$($SHA_CMD < "$canon" | awk '{print $1}')"
    h_vendored="$($SHA_CMD < "$vendored" | awk '{print $1}')"
    if [[ "$h_canon" != "$h_vendored" ]]; then
        echo "check-storage-seam-vendor-parity: DRIFT — $canon vs $vendored differ" >&2
        echo "  canonical $canon: $h_canon" >&2
        echo "  vendored  $vendored: $h_vendored" >&2
        echo "  Re-vendor with: cp $canon $vendored" >&2
        FAILED=1
    fi
done

if [[ "$FAILED" -ne 0 ]]; then
    exit 1
fi

echo "check-storage-seam-vendor-parity: clean (both vendored pairs are sha256-identical)"
exit 0
