#!/usr/bin/env bash
# check-no-auto-worktree.sh — assert no agentm code path auto-spawns a worktree (V5-10).
#
# LC-3 retires the old `worktrees-never-auto` prohibition: worktrees become a
# first-class, **operator-initiated** primitive (the spawn helper lives crickets-side).
# This gate is the agentm-side fence — it proves agentm's own automation surfaces never
# create a worktree unprompted. The binding "operator-initiated" enforcement is
# crickets' (the spawn helper); this guard only proves agentm itself does not auto-spawn.
#
# Scans executable surfaces (shell / python / powershell / CI yaml) for the worktree
# *spawn* subcommand. Read/cleanup subcommands (worktree list / remove / prune) are
# allowed — only the spawn verb is denied. Tests (`test_*.py`) and this gate's own file
# are excluded: tests are not automation code paths, and the negative test writes its
# offending fixture to a throwaway tree, never the repo.
#
# Usage:  bash scripts/check-no-auto-worktree.sh [--root DIR]
#   --root DIR   scan DIR instead of the repo root — the negative test points the gate
#                at a fixture tree carrying a deliberate spawn call.
# Exit:   0  no auto-spawn found
#         1  an automation surface spawns a worktree
#         2  setup error (root missing)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$REPO_ROOT"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --root=*) ROOT="${1#--root=}"; shift ;;
    *) echo "check-no-auto-worktree: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -d "$ROOT" ] || { echo "check-no-auto-worktree: not a directory: $ROOT" >&2; exit 2; }

# The worktree *spawn* verb: `git worktree add`. One-or-more blanks between tokens so
# reformatting can't slip past. This gate's own file is excluded from the scan (below),
# so naming the verb in these docs is safe.
SPAWN_RE='git[[:space:]]+worktree[[:space:]]+add'

# Curated automation surfaces: directories scanned recursively for executable-ish
# extensions, plus named root-level scripts. Prose (`*.md`) is not automation and is
# never scanned; design/plan docs that discuss worktrees can't trip this.
SCAN_DIRS=(scripts harness lib templates .github)
SCAN_FILES=(install.sh install.ps1)

fail=0
hits=""

scan_file() {
  local f="$1" base
  base="$(basename "$f")"
  case "$base" in
    test_*.py) return ;;                  # tests are not automation code paths
    check-no-auto-worktree.sh) return ;;  # this gate names the verb in its docs
  esac
  local m
  m="$(grep -nHE "$SPAWN_RE" "$f" 2>/dev/null || true)"
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
  done < <(find "$dir" -type f \
      \( -name '*.sh' -o -name '*.py' -o -name '*.ps1' -o -name '*.yml' -o -name '*.yaml' \) \
      2>/dev/null)
done
for f in "${SCAN_FILES[@]}"; do
  [ -f "$ROOT/$f" ] && scan_file "$ROOT/$f"
done

if [ "$fail" -ne 0 ]; then
  echo "check-no-auto-worktree: an agentm automation surface spawns a git worktree —" >&2
  printf '%s' "$hits" | sed 's/^/    /' >&2
  echo "" >&2
  echo "  agentm code must never create a worktree unprompted (LC-3: worktrees are" >&2
  echo "  operator-initiated; the spawn helper lives crickets-side). Remove the call" >&2
  echo "  or route it through an explicit operator action." >&2
  exit 1
fi

echo "check-no-auto-worktree: clean — no worktree-spawn in agentm automation surfaces."
exit 0
