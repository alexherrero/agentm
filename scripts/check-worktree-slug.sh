#!/usr/bin/env bash
# check-worktree-slug.sh — assert the worktree slug-safety invariant (V5-10).
#
# A worker runs in its own `git worktree`. A fresh worktree shares the parent's
# `.git` remotes but NOT the parent's gitignored `.harness/`, so the slug resolver's
# Tiers 1–2 (`.harness/project.json`) are invisible there and only Tier 3 (origin
# basename) survives. LC-2 makes Tier-3 the primary path on the constraint
# **slug == origin basename**. This gate is the executable enforcement: if the
# full-chain slug (an explicit `vault_project` / github.repo override) diverges from
# the origin basename, a worker in a worktree would silently write its plans/progress
# under the WRONG `projects/<slug>/` (parent Risk #1). The gate fails loudly on
# divergence rather than letting a wrong-slug write land.
#
# Delegates the comparison to `vault_project.py check-worktree-slug` (the shared
# resolver the `doctor` probe also calls) so the gate and the probe never drift.
#
# Usage:  bash scripts/check-worktree-slug.sh [--root DIR]
#   --root DIR   inspect DIR instead of the repo root — the negative test points the
#                gate at a divergent git fixture.
# Exit:   0  worktree-safe (slug == origin basename), OR no origin remote (warn-only)
#         1  DIVERGENT — a worktree would resolve to a different vault slug
#         2  setup error (python / resolver missing)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$REPO_ROOT"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --root=*) ROOT="${1#--root=}"; shift ;;
    *) echo "check-worktree-slug: unknown arg: $1" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "check-worktree-slug: $PY not found" >&2; exit 2; }
VP="$REPO_ROOT/scripts/vault_project.py"
[ -f "$VP" ] || { echo "check-worktree-slug: missing resolver: $VP" >&2; exit 2; }

# Always use the repo's own resolver; point it at ROOT as the project to inspect.
out="$("$PY" "$VP" check-worktree-slug "$ROOT" 2>&1)"
rc=$?

case "$rc" in
  0)
    echo "check-worktree-slug: $out"
    exit 0
    ;;
  3)
    # No origin remote — worktree-safety is unverifiable, but this is not a foot-gun
    # (a worktree would resolve to no slug and graceful-skip). Warn, do not fail.
    echo "check-worktree-slug: WARN — $out" >&2
    exit 0
    ;;
  1)
    echo "check-worktree-slug: $out" >&2
    echo "" >&2
    echo "  A worker in a git worktree cannot see this project's gitignored .harness/," >&2
    echo "  so it resolves the vault slug via the origin basename alone. Align the slug" >&2
    echo "  with the origin basename, or adopt the crickets worktree-spawn fallback that" >&2
    echo "  reproduces a divergent vault_project into the worktree." >&2
    echo "  See designs/v5-10-coordinator-team/ (LC-2)." >&2
    exit 1
    ;;
  *)
    echo "check-worktree-slug: resolver error (exit $rc): $out" >&2
    exit 2
    ;;
esac
