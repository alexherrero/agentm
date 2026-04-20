#!/usr/bin/env bash
# check-parity.sh — assert each adapter ships the canonical set of phase
# commands, sub-agents, and skills. Documents deliberate divergences.
#
# Canonical sets (derived from harness/phases/ + harness/agents/ + skills):
#   phase-commands: bugfix, plan, release, review, setup, work
#   sub-agents:     adversarial-reviewer, adversarial-reviewer-cross,
#                   documenter, explorer
#   skills:         dependabot-fixer
#
# Deliberate divergences (documented, not failures):
#   - codex wraps phase-commands as skills with `harness-` prefix
#     (its `/plan` and `/review` collide with built-in Codex commands).
#   - antigravity puts sub-agents under skills/ (Antigravity has no
#     separate sub-agent primitive).
#   - gemini has no skills/ dir; dependabot-fixer is reused from the
#     .agents/skills/ delivery (Codex adapter block).
#
# Each failure mode below documents how to reproduce by hand.
#
# Usage: bash scripts/check-parity.sh

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HARNESS_ROOT"

CANON_COMMANDS=(bugfix plan release review setup work)
CANON_AGENTS=(adversarial-reviewer adversarial-reviewer-cross documenter explorer)
CANON_SKILLS=(dependabot-fixer)

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
assert_set "claude-code/commands" adapters/claude-code/commands md "${CANON_COMMANDS[@]}"
assert_set "claude-code/agents"   adapters/claude-code/agents   md "${CANON_AGENTS[@]}"
assert_set "claude-code/skills"   adapters/claude-code/skills   ""  "${CANON_SKILLS[@]}"

echo "== antigravity =="
# Antigravity maps phase-commands to workflows/, and puts sub-agents under
# skills/ (no separate sub-agent primitive in Antigravity's surface).
assert_set "antigravity/workflows"          adapters/antigravity/workflows   md "${CANON_COMMANDS[@]}"
# skills/ = sub-agents + dependabot-fixer
expected_antigravity_skills=("${CANON_AGENTS[@]}" "${CANON_SKILLS[@]}")
assert_set "antigravity/skills"             adapters/antigravity/skills      ""  "${expected_antigravity_skills[@]}"
# exactly one always-on rules file
assert_set "antigravity/rules"              adapters/antigravity/rules       md  harness

echo "== codex =="
# Codex wraps phase-commands as skills with harness- prefix (collides with
# built-in /plan and /review otherwise); sub-agents are TOML.
codex_prefixed=()
for c in "${CANON_COMMANDS[@]}"; do codex_prefixed+=("harness-$c"); done
codex_skills=("${codex_prefixed[@]}" "${CANON_SKILLS[@]}")
assert_set "codex/skills"                   adapters/codex/skills            ""   "${codex_skills[@]}"
assert_set "codex/agents"                   adapters/codex/agents            toml "${CANON_AGENTS[@]}"

echo "== gemini =="
# Gemini has native slash commands + markdown sub-agents. No skills/ dir:
# dependabot-fixer is reused from .agents/skills/ (delivered by the codex
# block in install.sh/install.ps1).
assert_set "gemini/commands"                adapters/gemini/commands         toml "${CANON_COMMANDS[@]}"
assert_set "gemini/agents"                  adapters/gemini/agents           md   "${CANON_AGENTS[@]}"
if [[ -d adapters/gemini/skills ]]; then
  echo "FAIL [gemini]: adapters/gemini/skills exists — dependabot-fixer should be reused from .agents/skills/, not duplicated here" >&2
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
