#!/usr/bin/env bash
# check-parity.sh — assert each adapter ships the canonical set of skills and
# utility commands. Documents deliberate divergences.
#
# Canonical sets (post-V5 dev-loop slim — see ADR on the V5 unbundling):
#   skills:         doctor, migrate-to-diataxis
#                   (dependabot-fixer + ship-release migrated to crickets
#                    in v2.0.0 — see ADR 0006)
#   util-commands:  recent-wiki-changes  (claude-code only)
#
# The phase-gated dev loop (setup/plan/work/review/release/bugfix) and the
# three review sub-agents (adversarial-reviewer / -cross / explorer) are NO
# LONGER vendored by agentm — they moved to the crickets developer-workflows /
# code-review plugins in the V5 slim. agentm is unaware of them (DC-2): there
# is nothing to parity-check here. The adapters/{gemini,antigravity} command /
# workflow / agent dirs are intentionally gone; their absence is pinned by
# scripts/test_devloop_slim_retired.py, not by this parity check.
#
# Deliberate divergences (documented, not failures):
#   - gemini has no skills/ dir; shared skills (doctor,
#     migrate-to-diataxis) are delivered to `.agents/skills/` by
#     install.sh and Gemini reads that path natively per the Agent
#     Skills standard.
#
# Each failure mode below documents how to reproduce by hand.
#
# Usage: bash scripts/check-parity.sh

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HARNESS_ROOT"

CANON_SKILLS=(doctor migrate-to-diataxis)

# Utility slash commands (V4 #30 plan 2 task 7+) — claude-code only.
# Live in adapters/claude-code/commands/. Antigravity + Gemini operators
# invoke the underlying scripts directly.
CANON_UTIL_COMMANDS=(recent-wiki-changes)

fail=0

# Helper: names in a dir, filtered by extension (no ext → dirnames).
# Repro failure: add a rogue foo.md / foo.toml to the dir.
names_in() {
  local dir="$1" ext="$2"
  [[ -d "$dir" ]] || return 0
  if [[ -z "$ext" ]]; then
    ls "$dir" | while read -r n; do [[ -d "$dir/$n" ]] && echo "$n"; done | sort
  else
    ls "$dir"/*."$ext" 2>/dev/null | xargs -n1 basename | sed "s/\.$ext\$//" | sort
  fi
}

# Assert sorted name list matches expected set exactly.
# Repro failure: remove/rename/add a file to the dir.
assert_set() {
  local label="$1" dir="$2" ext="$3"
  shift 3
  local expected
  expected=$(printf '%s\n' "$@" | sort)
  local actual
  actual=$(names_in "$dir" "$ext")
  if [[ "$expected" != "$actual" ]]; then
    echo "FAIL [$label]: set mismatch in $dir" >&2
    diff <(echo "$expected") <(echo "$actual") | sed 's/^/    /' >&2
    fail=1
  else
    echo "    OK [$label] — ${#@} entries"
  fi
}

echo "== claude-code =="
# Claude-code commands = the utility commands only (the phase commands were
# slimmed out in V5; recent-wiki-changes is claude-code-only).
assert_set "claude-code/commands" adapters/claude-code/commands md "${CANON_UTIL_COMMANDS[@]}"
assert_set "claude-code/skills"   adapters/claude-code/skills   ""  "${CANON_SKILLS[@]}"

echo "== antigravity =="
# The always-on rules files: operating contract + AgentMemory vault context
# (V4 #22). The workflows/ + skills/ dirs were removed in the V5 dev-loop slim.
assert_set "antigravity/rules"              adapters/antigravity/rules       md  harness agentmemory-context

echo "== gemini =="
# Gemini has no skills/ dir: shared skills (doctor, migrate-to-diataxis) are
# delivered to `.agents/skills/` by install.sh and Gemini reads that path
# natively per the Agent Skills standard. The commands/ + agents/ dirs were
# removed in the V5 dev-loop slim.
if [[ -d adapters/gemini/skills ]]; then
  echo "FAIL [gemini]: adapters/gemini/skills exists — shared skills should be reused from .agents/skills/, not duplicated here" >&2
  fail=1
fi
if [[ ! -f adapters/gemini/settings.json ]]; then
  echo "FAIL [gemini]: adapters/gemini/settings.json is missing (AGENTS.md context.fileName wiring)" >&2
  fail=1
fi

if [[ $fail -ne 0 ]]; then
  echo ""
  echo "check-parity.sh: one or more adapter parity invariants failed."
  exit 1
fi
echo ""
echo "check-parity.sh: all adapters match the canonical set."
