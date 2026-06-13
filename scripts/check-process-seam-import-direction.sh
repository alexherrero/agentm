#!/usr/bin/env bash
# check-process-seam-import-direction.sh — assert the memory↔process edge is one-way (V5-4).
#
# LC-4: "Memory never imports the process." The process-seam client
# (`scripts/process_seam.py`) imports the memory engine (`harness_memory`); the
# engine must never import the seam back. A back-edge would turn the one-way
# client dependency into a cycle and let an engine change reach into process
# concerns — exactly what the seam exists to prevent.
#
# This gate is the executable enforcement. It scans agentm's Python automation
# surfaces for any module that imports `process_seam` and fails on a hit. Within
# agentm the seam's only legitimate importer is its own test suite — the designed
# consumers (the crickets developer-workflows phases, the V5-9 MCP server) live
# OUTSIDE this repo (LC-5), so any in-repo importer other than the tests is, by
# construction, the engine reaching for the seam. Excluded from the scan:
#   - `test_*.py`          — tests import the seam by design (that's what they test).
#   - `process_seam.py`    — the module itself (it imports the engine, not itself).
#   - the SEAM_CONSUMERS allowlist below — empty today; a future *reviewed*
#     in-repo process-side consumer is added here explicitly, so the back-edge
#     question is always answered in the open rather than by a silent gate edit.
#
# Usage:  bash scripts/check-process-seam-import-direction.sh [--root DIR]
#   --root DIR   scan DIR instead of the repo root — the negative test points the
#                gate at a fixture tree carrying a deliberate engine→seam import.
# Exit:   0  the edge is one-way (no engine module imports the seam)
#         1  a module imports process_seam (a forbidden back-edge)
#         2  setup error (root missing)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$REPO_ROOT"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --root=*) ROOT="${1#--root=}"; shift ;;
    *) echo "check-process-seam-import-direction: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -d "$ROOT" ] || { echo "check-process-seam-import-direction: not a directory: $ROOT" >&2; exit 2; }

# A Python import of the seam: `import process_seam[…]` or `from process_seam import …`.
# The trailing class rejects `process_seam_helper` (the `_` is a word char, so the
# name doesn't end at "seam") while accepting `import process_seam`, `… as seam`,
# `from process_seam import x`, and `import process_seam, other`. Modules in this
# tree import each other by bare name (sys.path insert), so the bare form is the
# real-world shape; a dotted `pkg.process_seam` is not how these modules import.
IMPORT_RE='^[[:space:]]*(import|from)[[:space:]]+process_seam([^A-Za-z0-9_]|$)'

# Reviewed in-repo process-side consumers allowed to import the seam. EMPTY today
# by design (consumers live crickets-side, LC-5). Add a basename here only with an
# explicit decision that the importer is process-side, never memory-engine.
SEAM_CONSUMERS=()

# Curated Python automation surfaces — where engine code lives. Only `*.py` is
# scanned: import-direction is a module-graph property, so shell / yaml / prose
# can't carry a back-edge and are never scanned.
SCAN_DIRS=(scripts harness lib templates .github)

fail=0
hits=""

is_allowed_consumer() {
  local base="$1" allowed
  for allowed in "${SEAM_CONSUMERS[@]:-}"; do
    [ "$base" = "$allowed" ] && return 0
  done
  return 1
}

scan_file() {
  local f="$1" base
  base="$(basename "$f")"
  case "$base" in
    test_*.py) return ;;       # tests import the seam by design
    process_seam.py) return ;; # the module itself (imports the engine, not itself)
  esac
  is_allowed_consumer "$base" && return  # reviewed process-side consumer
  local m
  m="$(grep -nHE "$IMPORT_RE" "$f" 2>/dev/null || true)"
  if [ -n "$m" ]; then
    hits+="$m"$'\n'
    fail=1
  fi
}

for d in "${SCAN_DIRS[@]}"; do
  dir="$ROOT/$d"
  [ -d "$dir" ] || continue
  while IFS= read -r f; do
    [ -n "$f" ] && scan_file "$f"
  done < <(find "$dir" -type f -name '*.py' 2>/dev/null)
done

if [ "$fail" -ne 0 ]; then
  echo "check-process-seam-import-direction: a module imports process_seam —" >&2
  printf '%s' "$hits" | sed 's/^/    /' >&2
  echo "" >&2
  echo "  The memory↔process edge must be one-directional: the process-seam client" >&2
  echo "  imports the memory engine, never the reverse (LC-4). A memory-engine module" >&2
  echo "  importing process_seam is a forbidden back-edge. Remove the import; if this" >&2
  echo "  is a deliberate process-side consumer, add its basename to SEAM_CONSUMERS." >&2
  exit 1
fi

echo "check-process-seam-import-direction: clean — the seam→engine edge is one-way."
exit 0
