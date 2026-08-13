#!/usr/bin/env bash
# check-parity.sh — assert each adapter ships the canonical set of skills and
# utility commands. Documents deliberate divergences.
#
# Canonical sets (post-V5 slim — see ADR on the V5 unbundling):
#   skills:         doctor
#                   (dependabot-fixer + ship-release migrated to crickets
#                    in v2.0.0 — see ADR 0006; the four-mode diataxis-migration
#                    skill retired to crickets' wiki-maintenance in the V5
#                    docs slim)
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
#   - gemini has no skills/ dir; the shared skill (doctor) is delivered
#     to `.agents/skills/` by install.sh and Gemini reads that path
#     natively per the Agent Skills standard.
#
# Each failure mode below documents how to reproduce by hand.
#
# Usage: bash scripts/check-parity.sh

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HARNESS_ROOT"

CANON_SKILLS=(doctor)

# Utility slash commands — the set is now EMPTY. `recent-wiki-changes` was the
# last one agentm vendored; it retired to crickets' `wiki` plugin (2026-08-12),
# which ships the command AND its own recent-wiki-changes.{sh,ps1} carrying a
# find_agentm_script resolver. agentm keeps scripts/recent-wiki-changes.{sh,ps1}
# as the direct-invocation surface for Antigravity + Gemini operators — only the
# claude-code slash command was a duplicate, and only it was retired.
CANON_UTIL_COMMANDS=()

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
# Claude-code commands: agentm ships NONE. The phase commands were slimmed out
# in V5; the last utility command (recent-wiki-changes) retired to crickets'
# `wiki` plugin in 2026-08-12's dupe retire. Asserted directly rather than via
# assert_set: expanding an empty array under `set -u` is an unbound-variable
# error on bash 3.2 (macOS's /bin/bash), so the empty case needs its own check
# — and this still catches a regression that re-vendors a command here.
if [[ -d adapters/claude-code/commands ]] && \
   compgen -G "adapters/claude-code/commands/*.md" >/dev/null; then
  echo "FAIL [claude-code/commands]: agentm vendors no claude-code commands, but found:" >&2
  ls adapters/claude-code/commands/*.md | sed 's/^/    /' >&2
  fail=1
else
  echo "    OK [claude-code/commands] — 0 entries (crickets-provided)"
fi
assert_set "claude-code/skills"   adapters/claude-code/skills   ""  "${CANON_SKILLS[@]}"

echo "== antigravity =="
# The always-on rules files: operating contract + AgentMemory vault context
# (V4 #22). The workflows/ + skills/ dirs were removed in the V5 dev-loop slim.
assert_set "antigravity/rules"              adapters/antigravity/rules       md  harness agentmemory-context

echo "== gemini =="
# Gemini has no skills/ dir: the shared skill (doctor) is delivered to
# `.agents/skills/` by install.sh and Gemini reads that path natively per
# the Agent Skills standard. The commands/ + agents/ dirs were removed in
# the V5 dev-loop slim.
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
