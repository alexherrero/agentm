#!/usr/bin/env bash
# install.sh — install or update agentm in a target project.
#
# Usage:
#   /path/to/agentm/install.sh [--hooks] [--update] <target-project-path>
#
# Options:
#   --hooks    Install the PostToolUse/PreCompact/SessionStart hooks into
#              .claude/settings.json and copy hook scripts to .harness/hooks/.
#              Merges idempotently with any existing settings.
#   --update   Refresh harness-authored files (commands, agents, skills,
#              hooks, scripts) to the current harness version. Leaves
#              user-authored files alone (PLAN.md, progress.md, verify.sh,
#              init.sh, known-migrations.md, AGENTS.md, CLAUDE.md).
#              Writes .harness/.version so future --update runs can show
#              the version delta.
#
# Without --update: existing files are preserved (skip-if-exists).
# With --update:    harness-authored files are overwritten; user files stay.

set -euo pipefail

# Installer boundary: this script copies ONLY from $HARNESS_ROOT/templates/
# and $HARNESS_ROOT/adapters/. The top-level $HARNESS_ROOT/wiki/ tree is
# this repo's own dogfooded documentation (how to use the harness) and
# must NEVER be propagated into target projects. Target projects get the
# empty scaffold from $HARNESS_ROOT/templates/wiki/ instead. Do not add
# copy paths that read from $HARNESS_ROOT/wiki/.
HARNESS_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
HARNESS_VERSION="$(git -C "$HARNESS_ROOT" describe --tags --abbrev=0 2>/dev/null || echo "dev")"

INSTALL_HOOKS=0
UPDATE_MODE=0
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --hooks) INSTALL_HOOKS=1 ;;
    --update) UPDATE_MODE=1 ;;
    -h|--help)
      sed -n 's/^# \{0,1\}//p' "$0" | head -22
      exit 0
      ;;
    -*)
      echo "Error: unknown flag: $arg" >&2
      exit 1
      ;;
    *)
      if [[ -n "$TARGET" ]]; then
        echo "Error: multiple target paths given" >&2
        exit 1
      fi
      TARGET="$arg"
      ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 [--hooks] [--update] <target-project-path>" >&2
  exit 1
fi

if [[ ! -d "$TARGET" ]]; then
  echo "Error: target directory does not exist: $TARGET" >&2
  exit 1
fi

cd "$TARGET"

# Detect existing install
EXISTING_VERSION=""
[[ -f .harness/.version ]] && EXISTING_VERSION="$(cat .harness/.version 2>/dev/null | tr -d '[:space:]')"

if [[ $UPDATE_MODE -eq 1 ]]; then
  if [[ -n "$EXISTING_VERSION" && "$EXISTING_VERSION" != "$HARNESS_VERSION" ]]; then
    echo "==> updating agentm in: $TARGET ($EXISTING_VERSION → $HARNESS_VERSION)"
  elif [[ -n "$EXISTING_VERSION" ]]; then
    echo "==> updating agentm in: $TARGET (already at $HARNESS_VERSION; refreshing managed files)"
  else
    echo "==> updating agentm in: $TARGET (no prior version recorded; treating as fresh refresh)"
  fi
else
  echo "==> installing agentm into: $TARGET (version $HARNESS_VERSION)"
  if [[ -n "$EXISTING_VERSION" && "$EXISTING_VERSION" != "$HARNESS_VERSION" ]]; then
    echo "    note: this project is on $EXISTING_VERSION; harness is $HARNESS_VERSION."
    echo "          re-run with --update to refresh harness-authored files."
  fi
fi

# ── shared install plumbing ────────────────────────────────────────────────
#
# Install primitives (ensure_boundary_src, cp_user, cp_managed, cp_user_walk,
# cp_managed_dir, sync_managed_parents) live in lib/install/bash/primitives.sh
# and are byte-identical to crickets's copy. See lib/install/CONTRACT.md
# for the caller contract.
#
# Caller-set variables the lib reads:
#   UPDATE_MODE     0|1 — managed-copy functions overwrite when 1
#   BOUNDARY_ROOTS  array of absolute paths — ensure_boundary_src accepts
#                   sources only from under these roots

BOUNDARY_ROOTS=(
  "$HARNESS_ROOT/templates"
  "$HARNESS_ROOT/adapters"
  # V4 #36: compound customizations imported from crickets live at
  # harness/{skills,hooks,agents,plugins}/. The new dispatcher block
  # (see "compound customizations" section below) reads from these.
  "$HARNESS_ROOT/harness/skills"
  "$HARNESS_ROOT/harness/hooks"
  "$HARNESS_ROOT/harness/agents"
)

# shellcheck source=lib/install/bash/primitives.sh
. "$HARNESS_ROOT/lib/install/bash/primitives.sh"

# ── --update sync: wipe fully-managed adapter dirs before recreate ──────────
#
# Why: cp_managed_dir refreshes content but never removes a dir that has been
# deleted from source (e.g. when an adapter is dropped — codex/v0.10.0).
# Without this wipe, --update leaves orphan files from the previous version's
# adapter set, and the local tree drifts from the GitHub source-of-truth.
#
# What's safe to wipe: these subdirs contain ONLY harness-authored content.
# User customizations go in crickets (roadmap #1) or in user-global
# ~/.claude/ paths. NEVER add a user-state path to this list — file-level
# entries at .harness/ root (PLAN.md, features.json, progress.md, init.sh,
# verify.sh, known-migrations.md) and settings.json files are file-level
# (not dirs) so wiping the dirs below does not touch them.
MANAGED_PARENTS=(
  .claude/commands
  .claude/agents
  .claude/skills
  .claude/hooks      # V4 #36: compound hooks (memory-*, evidence-tracker)
                     # imported from crickets land here via the manifest
                     # dispatcher.
  .agent/rules
  .agent/workflows
  .agent/skills
  .agents/skills
  .codex/agents      # legacy — wiped on first --update past v0.10.0
  .gemini/commands
  .gemini/agents
  .harness/scripts
  .harness/hooks
)

# Top-level dirs that should be removed if empty after wiping their managed
# subdirs. Keeps the tree clean after host removals (e.g. .codex/).
EMPTY_PARENT_CANDIDATES=(
  .codex
  .agents
)

if [[ $UPDATE_MODE -eq 1 ]]; then
  echo "==> sync mode: wiping fully-managed dirs before recreate from source"
  sync_managed_parents \
    "${MANAGED_PARENTS[@]}" \
    -- \
    "${EMPTY_PARENT_CANDIDATES[@]}"
fi

# ── user files: per-project state (never overwrite) ─────────────────────────

mkdir -p .harness
for f in PLAN.md features.json progress.md init.sh known-migrations.md; do
  cp_user "$HARNESS_ROOT/templates/$f" ".harness/$f"
done
chmod +x .harness/init.sh

# ── managed files: harness-authored (overwrite with --update) ───────────────

# .harness/scripts/ — helper scripts invoked by agents and phases.
# Ship BOTH .sh and .ps1 versions so mixed-OS teams get the right one.
mkdir -p .harness/scripts
for f in "$HARNESS_ROOT"/templates/scripts/*.sh; do
  [[ -e "$f" ]] || continue
  name="$(basename "$f")"
  cp_managed "$f" ".harness/scripts/$name"
  chmod +x ".harness/scripts/$name"
done
for f in "$HARNESS_ROOT"/templates/scripts/*.ps1; do
  [[ -e "$f" ]] || continue
  name="$(basename "$f")"
  cp_managed "$f" ".harness/scripts/$name"
done

# .claude/ — Claude Code config
mkdir -p .claude/commands .claude/agents .claude/skills
for f in "$HARNESS_ROOT"/adapters/claude-code/commands/*.md; do
  cp_managed "$f" ".claude/commands/$(basename "$f")"
done
for f in "$HARNESS_ROOT"/adapters/claude-code/agents/*.md; do
  cp_managed "$f" ".claude/agents/$(basename "$f")"
done
for d in "$HARNESS_ROOT"/adapters/claude-code/skills/*/; do
  [[ -d "$d" ]] || continue
  cp_managed_dir "$d" ".claude/skills/$(basename "$d")"
done

# .agent/ — Antigravity config (full-parity adapter). Copies workflows,
# skills, and the always-on rules file into the target's .agent/ tree.
mkdir -p .agent/rules .agent/workflows .agent/skills
for f in "$HARNESS_ROOT"/adapters/antigravity/rules/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".agent/rules/$(basename "$f")"
done
for f in "$HARNESS_ROOT"/adapters/antigravity/workflows/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".agent/workflows/$(basename "$f")"
done
for d in "$HARNESS_ROOT"/adapters/antigravity/skills/*/; do
  [[ -d "$d" ]] || continue
  cp_managed_dir "$d" ".agent/skills/$(basename "$d")"
done

# .agents/skills/ — shared skills delivery (read by Gemini CLI per the
# Agent Skills standard). Source: adapters/claude-code/skills/. The
# shared skills are duplicated across per-host adapter dirs (parity
# enforces identical content) and claude-code/skills/ is the cleanest
# source — antigravity/skills/ mixes sub-agents-as-skills with shared
# skills, so iterating that dir would over-deliver.
mkdir -p .agents/skills
for name in doctor migrate-to-diataxis; do
  src="$HARNESS_ROOT/adapters/claude-code/skills/$name"
  [[ -d "$src" ]] || continue
  cp_managed_dir "$src" ".agents/skills/$name"
done

# .gemini/ — Gemini CLI config (full-parity adapter). Commands are TOML,
# subagents are markdown w/ YAML frontmatter. settings.json uses cp_user
# semantics (never clobber existing user config — README documents the
# AGENTS.md fileName merge if they already have a settings.json).
# Note: Gemini reads shared skills from .agents/skills/ (delivered above)
# per the Agent Skills standard.
mkdir -p .gemini/commands .gemini/agents
for f in "$HARNESS_ROOT"/adapters/gemini/commands/*.toml; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".gemini/commands/$(basename "$f")"
done
for f in "$HARNESS_ROOT"/adapters/gemini/agents/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".gemini/agents/$(basename "$f")"
done
cp_user "$HARNESS_ROOT/adapters/gemini/settings.json" ".gemini/settings.json"

# ── wiki/ — documentation scaffold (per-file walk, skip-if-exists) ──────────
# Source: $HARNESS_ROOT/templates/wiki/ (NOT $HARNESS_ROOT/wiki/ — that's
# this repo's own dogfooded docs and never ships to targets).
cp_user_walk "$HARNESS_ROOT/templates/wiki" "wiki"

# ── compound customizations (V4 #36) ───────────────────────────────────────
#
# Walks harness/skills/<dir>/SKILL.md, harness/hooks/<dir>/hook.md, and
# harness/agents/<file>.md, dispatching each based on its supported_hosts
# field. Imported from crickets in v4.0.0 per design call #28 of plan #18:
# compound skills (memory, design, diataxis-author, ship-release), memory
# hooks (memory-recall-*, memory-reflect-*), the evidence-tracker hook,
# and the memory-idea-researcher sub-agent.
#
# Only dispatches entries with crickets-shape frontmatter (kind: <type> +
# supported_hosts: <list>). Legacy agentm single-file skills (doctor.md,
# migrate-to-diataxis.md) and legacy sub-agents (adversarial-reviewer.md
# etc.) at harness/skills/*.md and harness/agents/*.md without frontmatter
# flow through the adapters/ pipeline above and are skipped here.

_am_get_field() {
    # Cheap YAML field extractor — keeps dispatch independent of pyyaml.
    # Reads the first value of <field>: from the file's YAML frontmatter.
    # Strips list brackets so `[claude-code, antigravity]` returns
    # `claude-code, antigravity`.
    local file="$1" field="$2"
    awk -v field="$field" '
        BEGIN { in_fm = 0; first_dash_seen = 0 }
        /^---$/ {
            if (!first_dash_seen) { first_dash_seen = 1; in_fm = 1; next }
            in_fm = 0; exit
        }
        in_fm {
            if (match($0, "^" field "[[:space:]]*:[[:space:]]*")) {
                v = substr($0, RLENGTH + 1)
                sub(/[[:space:]]*#.*$/, "", v)
                gsub(/^\[|\]$/, "", v)
                print v
                exit
            }
        }
    ' "$file"
}

_am_dispatch_skill() {
    local skill_dir="$1" name="$2" hosts="$3"
    local h
    while IFS= read -r h; do
        h="${h## }"; h="${h%% }"
        [[ -z "$h" ]] && continue
        case "$h" in
            claude-code)
                mkdir -p .claude/skills
                cp_managed_dir "$skill_dir" ".claude/skills/$name"
                ;;
            antigravity)
                mkdir -p .agents/skills
                cp_managed_dir "$skill_dir" ".agents/skills/$name"
                ;;
            *)
                echo "    WARN: skill '$name': unknown host '$h' — skipped" >&2
                ;;
        esac
    done < <(echo "$hosts" | tr ',' '\n')
}

_am_dispatch_hook() {
    local hook_dir="$1" name="$2" hosts="$3"
    local h
    while IFS= read -r h; do
        h="${h## }"; h="${h%% }"
        [[ -z "$h" ]] && continue
        case "$h" in
            claude-code)
                mkdir -p .claude/hooks
                local script_src="$hook_dir/$name.sh"
                if [[ ! -f "$script_src" ]]; then
                    echo "    WARN: hook '$name' missing $name.sh — skipped" >&2
                    continue
                fi
                cp_managed "$script_src" ".claude/hooks/$name.sh"
                chmod +x ".claude/hooks/$name.sh"
                # Copy sibling .py helpers (evidence-tracker ships
                # evidence_tracker.py alongside its .sh).
                local py
                for py in "$hook_dir"/*.py; do
                    [[ -e "$py" ]] || continue
                    cp_managed "$py" ".claude/hooks/$(basename "$py")"
                done
                # Merge bash settings fragment idempotently.
                local frag="$hook_dir/settings-fragment-bash.json"
                if [[ -f "$frag" ]] && command -v python3 >/dev/null 2>&1; then
                    mkdir -p .claude
                    python3 "$HARNESS_ROOT/scripts/merge-settings-fragment.py" \
                        .claude/settings.json "$frag" 2>/dev/null \
                        || echo "    WARN: failed to merge settings fragment for hook '$name'" >&2
                fi
                ;;
            antigravity)
                # Antigravity has no first-class hook surface (ADR 0009).
                # Silently skip — hook author opted into both hosts; we
                # honor claude-code and no-op the antigravity side.
                ;;
            *)
                echo "    WARN: hook '$name': unknown host '$h' — skipped" >&2
                ;;
        esac
    done < <(echo "$hosts" | tr ',' '\n')
}

_am_dispatch_agent() {
    local agent_md="$1" name="$2" hosts="$3"
    local h
    while IFS= read -r h; do
        h="${h## }"; h="${h%% }"
        [[ -z "$h" ]] && continue
        case "$h" in
            claude-code)
                mkdir -p .claude/agents
                cp_managed "$agent_md" ".claude/agents/$name.md"
                ;;
            antigravity)
                # Sub-agent-as-skill pattern (no first-class sub-agent slot
                # in Antigravity 2.0 — wrap the agent md as a skill).
                mkdir -p ".agents/skills/$name"
                cp_managed "$agent_md" ".agents/skills/$name/SKILL.md"
                ;;
            *)
                echo "    WARN: agent '$name': unknown host '$h' — skipped" >&2
                ;;
        esac
    done < <(echo "$hosts" | tr ',' '\n')
}

# Walk compound skills.
if [[ -d "$HARNESS_ROOT/harness/skills" ]]; then
    for _am_d in "$HARNESS_ROOT/harness/skills"/*/; do
        [[ -d "$_am_d" ]] || continue
        _am_manifest="${_am_d}SKILL.md"
        [[ -f "$_am_manifest" ]] || continue
        _am_kind="$(_am_get_field "$_am_manifest" kind)"
        [[ "$_am_kind" == "skill" ]] || continue
        _am_name="$(basename "$_am_d")"
        _am_hosts="$(_am_get_field "$_am_manifest" supported_hosts)"
        if [[ -z "$_am_hosts" ]]; then
            echo "    WARN: skill '$_am_name' has no supported_hosts — skipped" >&2
            continue
        fi
        echo "==> installing compound skill: $_am_name"
        _am_dispatch_skill "${_am_d%/}" "$_am_name" "$_am_hosts"
    done
fi

# Walk compound hooks.
if [[ -d "$HARNESS_ROOT/harness/hooks" ]]; then
    for _am_d in "$HARNESS_ROOT/harness/hooks"/*/; do
        [[ -d "$_am_d" ]] || continue
        _am_manifest="${_am_d}hook.md"
        [[ -f "$_am_manifest" ]] || continue
        _am_kind="$(_am_get_field "$_am_manifest" kind)"
        [[ "$_am_kind" == "hook" ]] || continue
        _am_name="$(basename "$_am_d")"
        _am_hosts="$(_am_get_field "$_am_manifest" supported_hosts)"
        if [[ -z "$_am_hosts" ]]; then
            echo "    WARN: hook '$_am_name' has no supported_hosts — skipped" >&2
            continue
        fi
        echo "==> installing compound hook: $_am_name"
        _am_dispatch_hook "${_am_d%/}" "$_am_name" "$_am_hosts"
    done
fi

# Walk compound agents. Only crickets-shape manifests (with kind: agent in
# frontmatter) are dispatched; legacy agentm sub-agents at
# harness/agents/*.md without frontmatter flow through adapters/.
if [[ -d "$HARNESS_ROOT/harness/agents" ]]; then
    for _am_f in "$HARNESS_ROOT/harness/agents"/*.md; do
        [[ -f "$_am_f" ]] || continue
        _am_kind="$(_am_get_field "$_am_f" kind)"
        [[ "$_am_kind" == "agent" ]] || continue
        _am_name="$(basename "$_am_f" .md)"
        _am_hosts="$(_am_get_field "$_am_f" supported_hosts)"
        if [[ -z "$_am_hosts" ]]; then
            echo "    WARN: agent '$_am_name' has no supported_hosts — skipped" >&2
            continue
        fi
        echo "==> installing compound agent: $_am_name"
        _am_dispatch_agent "$_am_f" "$_am_name" "$_am_hosts"
    done
fi

unset _am_d _am_f _am_manifest _am_kind _am_name _am_hosts

# ── .github/workflows/wiki-sync.yml — managed (refreshed on --update) ───────
mkdir -p .github/workflows
cp_managed "$HARNESS_ROOT/templates/.github/workflows/wiki-sync.yml" ".github/workflows/wiki-sync.yml"

# ── user files: top-level entrypoints (never overwrite) ─────────────────────

if [[ ! -e AGENTS.md ]]; then
  cp "$HARNESS_ROOT/AGENTS.md" AGENTS.md
  echo "    created AGENTS.md"
else
  echo "    kept    AGENTS.md (exists — you may want to merge harness sections from $HARNESS_ROOT/AGENTS.md)"
fi

if [[ ! -e CLAUDE.md ]]; then
  cp "$HARNESS_ROOT/CLAUDE.md" CLAUDE.md
  echo "    created CLAUDE.md"
else
  echo "    kept    CLAUDE.md (exists)"
fi

# ── --hooks: install PostToolUse/PreCompact/SessionStart hooks ──────────────

if [[ $INSTALL_HOOKS -eq 1 ]]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "Error: --hooks requires jq (for merging settings.json). Install jq and re-run." >&2
    exit 1
  fi

  # verify.* — per-project (user-editable). Ship both .sh and .ps1.
  cp_user "$HARNESS_ROOT/templates/verify.sh"  .harness/verify.sh
  cp_user "$HARNESS_ROOT/templates/verify.ps1" .harness/verify.ps1
  chmod +x .harness/verify.sh

  # hook scripts — harness-authored (managed). Ship both .sh and .ps1.
  mkdir -p .harness/hooks
  for f in precompact.sh session-start-compact.sh; do
    cp_managed "$HARNESS_ROOT/templates/hooks/$f" ".harness/hooks/$f"
    chmod +x ".harness/hooks/$f"
  done
  for f in precompact.ps1 session-start-compact.ps1; do
    cp_managed "$HARNESS_ROOT/templates/hooks/$f" ".harness/hooks/$f"
  done

  # Hook registrations — read canonical bash fragment from templates/hooks/
  # (kept in lockstep with templates/hooks/settings-fragment-pwsh.json for
  # the PowerShell installer). Merged into .claude/settings.json per event.
  HOOK_FRAGMENT=$(cat "$HARNESS_ROOT/templates/hooks/settings-fragment-bash.json")

  if [[ ! -e .claude/settings.json ]]; then
    echo "$HOOK_FRAGMENT" > .claude/settings.json
    echo "    created .claude/settings.json with harness hooks (verify + precompact + session-start)"
  else
    merged=$(jq -s '
      def has_cmd($needle):
        [.. | objects | .command? // empty | strings | select(contains($needle))] | any;

      .[0] as $existing | .[1] as $fragment |
      reduce ($fragment.hooks | to_entries[]) as $e ($existing;
        . as $cur |
        ($e.value[0]) as $new_entry |
        ($new_entry.hooks[0].command) as $needle |
        if (($cur.hooks // {})[$e.key] // []) | has_cmd($needle)
        then $cur
        else
          .hooks //= {} |
          .hooks[$e.key] //= [] |
          .hooks[$e.key] += [$new_entry]
        end
      )
    ' .claude/settings.json <(echo "$HOOK_FRAGMENT") 2>/dev/null)

    if [[ -z "$merged" ]]; then
      echo "    WARNING: failed to merge hooks into .claude/settings.json. Add manually:" >&2
      echo "$HOOK_FRAGMENT" | sed 's/^/      /' >&2
    else
      added=$(diff <(jq -S . .claude/settings.json) <(echo "$merged" | jq -S .) | grep -c '^>' || true)
      echo "$merged" > .claude/settings.json
      if [[ "$added" -eq 0 ]]; then
        echo "    kept    .claude/settings.json (all harness hooks already present)"
      else
        echo "    updated .claude/settings.json (added missing harness hooks)"
      fi
    fi
  fi

  echo ""
  echo "==> hooks installed:"
  echo "    - PostToolUse  → .harness/verify.sh (per-file verification on Write/Edit)"
  echo "    - PreCompact   → .harness/hooks/precompact.sh (writes marker to progress.md)"
  echo "    - SessionStart → .harness/hooks/session-start-compact.sh (re-anchors after compact)"
  echo "    Edit .harness/verify.sh to enable checks for your stack."
fi

# ── probe + persist install state (V4 #30 task 3) ──────────────────────────
# Detect whether the operator has source-clone canonical paths for agentm +
# crickets; persist the decision to <install-prefix>/.agentm-install-state.json.
# Silent — no stdout (the helper emits the persist target, captured to /dev/null
# here so the existing install logs aren't disturbed). Decision drives the
# source-vs-release dispatch in tasks 4-5.

if command -v python3 >/dev/null 2>&1; then
  python3 "$HARNESS_ROOT/scripts/install_state.py" persist \
    .claude \
    --harness-version "$HARNESS_VERSION" >/dev/null 2>&1 || true
fi

# ── record version ──────────────────────────────────────────────────────────

echo "$HARNESS_VERSION" > .harness/.version

# ── done ────────────────────────────────────────────────────────────────────

echo ""
if [[ $UPDATE_MODE -eq 1 ]]; then
  echo "==> update complete (now at $HARNESS_VERSION)."
else
  echo "==> done."
  echo ""
  echo "Next steps:"
  echo "  1. Edit .harness/init.sh so it actually boots this project"
  if [[ $INSTALL_HOOKS -eq 1 ]]; then
    echo "  2. Edit .harness/verify.sh — uncomment the language case for your stack"
    echo "  3. Run /setup (Claude Code) or prompt 'run the setup phase' (Antigravity)"
    echo "  4. Then /plan <your first brief>"
  else
    echo "  2. Run /setup (Claude Code) or prompt 'run the setup phase' (Antigravity)"
    echo "  3. Then /plan <your first brief>"
  fi
fi
