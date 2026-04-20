#!/usr/bin/env bash
# install.sh — install or update agentic-harness in a target project.
#
# Usage:
#   /path/to/agentic-harness/install.sh [--hooks] [--update] <target-project-path>
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
    echo "==> updating agentic-harness in: $TARGET ($EXISTING_VERSION → $HARNESS_VERSION)"
  elif [[ -n "$EXISTING_VERSION" ]]; then
    echo "==> updating agentic-harness in: $TARGET (already at $HARNESS_VERSION; refreshing managed files)"
  else
    echo "==> updating agentic-harness in: $TARGET (no prior version recorded; treating as fresh refresh)"
  fi
else
  echo "==> installing agentic-harness into: $TARGET (version $HARNESS_VERSION)"
  if [[ -n "$EXISTING_VERSION" && "$EXISTING_VERSION" != "$HARNESS_VERSION" ]]; then
    echo "    note: this project is on $EXISTING_VERSION; harness is $HARNESS_VERSION."
    echo "          re-run with --update to refresh harness-authored files."
  fi
fi

# ── helpers ─────────────────────────────────────────────────────────────────

# cp_user: copy only if destination is missing. For files the user owns and edits.
cp_user() {
  local src="$1" dst="$2"
  if [[ ! -e "$dst" ]]; then
    cp "$src" "$dst"
    echo "    created $dst"
  else
    echo "    kept    $dst (exists)"
  fi
}

# cp_managed: in --update mode, always overwrite. Otherwise, skip if exists.
# For harness-authored files the user should not edit.
cp_managed() {
  local src="$1" dst="$2"
  if [[ $UPDATE_MODE -eq 1 && -e "$dst" ]]; then
    if cmp -s "$src" "$dst"; then
      echo "    kept    $dst (up to date)"
    else
      cp "$src" "$dst"
      echo "    updated $dst"
    fi
  elif [[ ! -e "$dst" ]]; then
    cp "$src" "$dst"
    echo "    created $dst"
  else
    echo "    kept    $dst (exists — re-run with --update to refresh)"
  fi
}

# cp_user_walk: walk a source directory recursively and cp_user each file.
# Preserves any files the user has already created in the destination tree;
# fills in missing scaffold files without clobbering. Used for wiki/ where
# scaffold and human-authored pages coexist.
cp_user_walk() {
  local src_root="$1" dst_root="$2"
  [[ -d "$src_root" ]] || return 0
  # Portable: find prints src paths; strip prefix to get relative.
  while IFS= read -r src_file; do
    local rel="${src_file#"$src_root"/}"
    local dst_file="$dst_root/$rel"
    mkdir -p "$(dirname "$dst_file")"
    cp_user "$src_file" "$dst_file"
  done < <(find "$src_root" -type f)
}

# cp_managed_dir: same semantics for directory skills.
cp_managed_dir() {
  local src="$1" dst="$2"
  if [[ $UPDATE_MODE -eq 1 && -e "$dst" ]]; then
    # Overwrite directory contents. Safe because skills are harness-authored.
    rm -rf "$dst"
    cp -R "$src" "$dst"
    echo "    updated $dst"
  elif [[ ! -e "$dst" ]]; then
    cp -R "$src" "$dst"
    echo "    created $dst"
  else
    echo "    kept    $dst (exists — re-run with --update to refresh)"
  fi
}

# ── user files: per-project state (never overwrite) ─────────────────────────

mkdir -p .harness
for f in PLAN.md features.json progress.md init.sh known-migrations.md; do
  cp_user "$HARNESS_ROOT/templates/$f" ".harness/$f"
done
chmod +x .harness/init.sh

# ── managed files: harness-authored (overwrite with --update) ───────────────

# .harness/scripts/ — helper scripts invoked by agents and phases
mkdir -p .harness/scripts
for f in "$HARNESS_ROOT"/templates/scripts/*.sh; do
  [[ -e "$f" ]] || continue
  name="$(basename "$f")"
  cp_managed "$f" ".harness/scripts/$name"
  chmod +x ".harness/scripts/$name"
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

# .agents/skills/ + .codex/agents/ — Codex CLI config (full-parity adapter).
# Note: Codex uses plural .agents/ for skills and singular .codex/ for
# subagents (TOML) — distinct from Antigravity's .agent/ singular.
mkdir -p .agents/skills .codex/agents
for d in "$HARNESS_ROOT"/adapters/codex/skills/*/; do
  [[ -d "$d" ]] || continue
  cp_managed_dir "$d" ".agents/skills/$(basename "$d")"
done
for f in "$HARNESS_ROOT"/adapters/codex/agents/*.toml; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".codex/agents/$(basename "$f")"
done

# .gemini/ — Gemini CLI config (full-parity adapter). Commands are TOML,
# subagents are markdown w/ YAML frontmatter. settings.json uses cp_user
# semantics (never clobber existing user config — README documents the
# AGENTS.md fileName merge if they already have a settings.json).
# Note: dependabot-fixer lives in .agents/skills/ (copied by Codex block
# above); Gemini reads that path natively per the Agent Skills standard.
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

  # verify.sh — per-project (user-editable)
  cp_user "$HARNESS_ROOT/templates/verify.sh" .harness/verify.sh
  chmod +x .harness/verify.sh

  # hook scripts — harness-authored (managed)
  mkdir -p .harness/hooks
  for f in precompact.sh session-start-compact.sh; do
    cp_managed "$HARNESS_ROOT/templates/hooks/$f" ".harness/hooks/$f"
    chmod +x ".harness/hooks/$f"
  done

  # Hook registrations — merge into .claude/settings.json idempotently per event.
  HOOK_FRAGMENT=$(cat <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // .tool_response.filePath // empty' | { read -r f; [[ -n \"$f\" && -x .harness/verify.sh ]] && bash .harness/verify.sh \"$f\" || true; }",
            "timeout": 10
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "manual|auto",
        "hooks": [
          {
            "type": "command",
            "command": "bash .harness/hooks/precompact.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "bash .harness/hooks/session-start-compact.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
JSON
)

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
