#!/usr/bin/env bash
# check-workflow-parity.sh — every templated GitHub Actions workflow is active at
# the repo root, byte-identical to its template (the dogfood self-consumption invariant).
#
# agentm ships its CI workflows as templates under templates/.github/workflows/ AND
# consumes them at .github/workflows/ — it dogfoods its own harness. The two copies
# must stay byte-identical: editing one twin without the other is "drift", and it
# silently breaks either the template a target project installs or this repo's own CI.
#
# This gate is the LOCAL mirror of the Linux-only `dogfood-workflows` CI job. It exists
# to close a real gap: a workflow edited on only one side passed the whole local battery
# (the dogfood check was CI-inline, never in check-all.sh) and the drift only surfaced
# after a push, as a red Linux run. Running the same assertion locally catches it before
# the commit.
#
# For each templates/.github/workflows/*.yml it asserts the active
# .github/workflows/<name> exists and is byte-identical (diff -u, matching the CI step
# exactly — same comparator, so the local and CI verdicts can't diverge). Active
# workflows WITHOUT a template twin (e.g. ci-all.yml) are out of scope by design: the
# invariant is template→active, not the reverse.
#
# One deliberate divergence from CI: finding ZERO templated workflows is a setup error
# (exit 2) here, not the vacuous exit-0 CI's `nullglob` loop yields. A local gate that
# silently checked nothing must not read as green — CI runs in a known-good checkout
# where the template surface always exists; the local battery benefits from the louder
# guard (a vanished templates/.github/workflows/ is itself a regression worth failing on).
#
# Usage:  bash scripts/check-workflow-parity.sh [--root DIR]
#   --root DIR   scan DIR instead of the repo root — the negative test points the gate
#                at a fixture tree carrying a deliberately drifted twin.
# Exit:   0  every templated workflow is active at root + byte-identical
#         1  a templated workflow is missing at root, or drifted from its template
#         2  setup error (root missing, or no templated workflows found)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$REPO_ROOT"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --root=*) ROOT="${1#--root=}"; shift ;;
    *) echo "check-workflow-parity: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -d "$ROOT" ] || { echo "check-workflow-parity: not a directory: $ROOT" >&2; exit 2; }

TEMPLATE_DIR="$ROOT/templates/.github/workflows"
ACTIVE_DIR="$ROOT/.github/workflows"

fail=0
checked=0
problems=""

# nullglob so a non-matching '*.yml' expands to nothing (never leaks the literal
# pattern) — an empty or absent template dir yields checked=0, handled below.
shopt -s nullglob
for tmpl in "$TEMPLATE_DIR"/*.yml; do
  name="$(basename "$tmpl")"
  active="$ACTIVE_DIR/$name"
  checked=$((checked+1))
  if [ ! -f "$active" ]; then
    problems+="  missing: .github/workflows/$name — shipped as a template but not active at the repo root"$'\n'
    fail=1
    continue
  fi
  if ! diff -u "$tmpl" "$active" >/dev/null 2>&1; then
    problems+="  drifted: .github/workflows/$name differs from templates/.github/workflows/$name"$'\n'
    fail=1
  fi
done

if [ "$checked" -eq 0 ]; then
  echo "check-workflow-parity: no templated workflows found under templates/.github/workflows/ —" >&2
  echo "  wrong --root, or the template surface vanished? A parity gate that checks nothing is not green." >&2
  exit 2
fi

if [ "$fail" -ne 0 ]; then
  echo "check-workflow-parity: templated workflow(s) out of sync with the active copy —" >&2
  printf '%s' "$problems" >&2
  echo "" >&2
  echo "  Re-sync the twins so both carry the change, e.g.:" >&2
  echo "    cp .github/workflows/<name> templates/.github/workflows/<name>   # (or the reverse)" >&2
  echo "  Then re-run. This mirrors the Linux 'dogfood-workflows' CI job." >&2
  exit 1
fi

echo "check-workflow-parity: clean — $checked templated workflow(s) active at root, byte-identical."
exit 0
