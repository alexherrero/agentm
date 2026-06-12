#!/usr/bin/env bash
# install.sh — install or update agentm in a target project.
#
# Usage:
#   /path/to/agentm/install.sh [--hooks] [--update] [--local-state] <target-project-path>
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
#   --local-state  Opt this machine into repo-local (vault-less) harness state:
#              writes "state_mode": "local" to .agentm-config.json (the on-host
#              config; DC-8) and skips vault auto-detection. State then lives in
#              <repo>/.harness/ instead of a MemoryVault.
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
FORCE_VAULT_PROMPT=0   # v4.5.1 task 4: re-fire first-run vault prompt
LOCAL_STATE=0          # Hardening I #44 task 4: --local-state → repo-local (vault-less) state
TARGET=""
SCOPE="project"  # V4 #30 task 8: --scope user|project. Default 'project' for
                 # v4.3.0 backward compat; default flips to 'user' in a future
                 # release once dogfood (task 11) confirms the new path works.

# Parse with shift-based loop to handle `--scope user` (value follows flag)
while [[ $# -gt 0 ]]; do
  arg="$1"
  case "$arg" in
    --hooks) INSTALL_HOOKS=1; shift ;;
    --update) UPDATE_MODE=1; shift ;;
    --force-vault-prompt) FORCE_VAULT_PROMPT=1; shift ;;
    --local-state) LOCAL_STATE=1; shift ;;
    --scope)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --scope requires a value (user|project)" >&2
        exit 1
      fi
      SCOPE="$2"
      if [[ "$SCOPE" != "user" && "$SCOPE" != "project" ]]; then
        echo "Error: --scope must be 'user' or 'project', got: $SCOPE" >&2
        exit 1
      fi
      shift 2
      ;;
    --scope=*)
      SCOPE="${arg#--scope=}"
      if [[ "$SCOPE" != "user" && "$SCOPE" != "project" ]]; then
        echo "Error: --scope must be 'user' or 'project', got: $SCOPE" >&2
        exit 1
      fi
      shift
      ;;
    -h|--help)
      sed -n 's/^# \{0,1\}//p' "$0" | head -23
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
      shift
      ;;
  esac
done

# --scope user doesn't require a positional TARGET (install prefix is ~/.claude/);
# --scope project requires one.
if [[ "$SCOPE" == "project" && -z "$TARGET" ]]; then
  echo "Usage: $0 [--hooks] [--update] [--local-state] [--scope user|project] <target-project-path>" >&2
  echo "  --scope user: install customizations to ~/.claude/ (target not required)" >&2
  echo "  --scope project (default): install to <target>/.claude/" >&2
  exit 1
fi

# Hardening I #44 task 4: --local-state threads `--state-mode local` into the
# install-state persist call (both --scope user and --scope project) so
# .agentm-config.json becomes the on-host source of truth for repo-local,
# vault-less harness state (DC-8). Empty array when not set; every expansion
# below uses the `+` guard so `set -u` + bash 3.2 (macOS) don't trip on an
# empty-array expansion.
PERSIST_STATE_MODE_ARGS=()
if [[ $LOCAL_STATE -eq 1 ]]; then
  PERSIST_STATE_MODE_ARGS=(--state-mode local)
fi

# ── crickets-sibling bootstrap: REMOVED (crickets v3.0 #40 part 5) ──────────
# agentm's installer no longer auto-clones + invokes crickets's install.sh —
# crickets dropped its bespoke per-host installer in favor of NATIVE plugins
# (Claude Code / Antigravity marketplaces). Operators install crickets via its
# one-line bootstrap (`bash ~/Antigravity/crickets/bootstrap.sh`) or the host's
# native `plugin install`. The two repos are now decoupled at install time.

# ── first-run vault detection (v4.5.1 task 4) ───────────────────────────────
# Probe likely Obsidian-vault locations under Google Drive, present numbered
# candidates to the operator, write the chosen path to .agentm-config.json
# via agentm_config.py. Triggers on --scope user installs when vault_path is
# unset OR --force-vault-prompt is passed. CI-skipped via $CI=true env.
#
# Out of scope (deferred to a follow-up if the contributor base needs it):
# Windows + Linux auto-detect. macOS-only for now per locked DC-7.

_agentm_vault_first_run_prompt() {
    local prefix="$1"
    # CI skip — runners don't have an interactive operator + don't host
    # vaults. Emit a one-line notice; don't pollute stderr otherwise.
    if [[ "${CI:-}" == "true" ]]; then
        echo "    vault prompt: CI detected; skipping (set via agentm_config.py --vault-path if needed)"
        return 0
    fi
    # Skip if already set + not forced.
    local existing
    existing="$(AGENTM_INSTALL_PREFIX="$prefix" python3 "$HARNESS_ROOT/scripts/agentm_config.py" --get vault_path 2>/dev/null || true)"
    if [[ -n "$existing" && $FORCE_VAULT_PROMPT -eq 0 ]]; then
        echo "    vault_path: $existing (use --force-vault-prompt to re-select)"
        return 0
    fi

    # Only probe on macOS — Windows + Linux defer to manual --vault-path.
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "    vault prompt: non-Darwin host; skipping auto-detect (set via agentm_config.py --vault-path)"
        return 0
    fi

    echo "==> detecting Obsidian vaults under ~/Library/CloudStorage/GoogleDrive-*/"
    # Bounded probe: max-depth 4, 10s hard timeout, looking for either a
    # repo_registry marker (V4 #30 plan 1) or an active Obsidian vault dir.
    local candidates=()
    local probe_root="$HOME/Library/CloudStorage"
    if [[ -d "$probe_root" ]]; then
        # macOS doesn't ship GNU `timeout`. Try gtimeout, then fall back to no-timeout.
        local _timeout_cmd=""
        if command -v gtimeout >/dev/null 2>&1; then
            _timeout_cmd="gtimeout 10s"
        elif command -v timeout >/dev/null 2>&1; then
            _timeout_cmd="timeout 10s"
        fi
        # Find directories containing _meta/repos.json (vault marker) OR .obsidian/.
        # Match parent dir of either marker. Bounded by max-depth 5 to allow the
        # marker dir to be 1 level deeper than the vault root.
        #
        # `-L` follows symlinks — Google Drive serves shortcut targets via the
        # `.shortcut-targets-by-id/<id>/...` tree, and operators sometimes
        # access vaults through plain symlinks too. The -maxdepth 5 cap +
        # the marker-only -print contains any worst-case symlink-loop blast
        # radius.
        #
        # Prune common noise dirs that host stray markers but never a real
        # vault: trashes (`.Trash`, `.Trash-NNN`, `.Trashes`), Google Drive +
        # macOS scratch (`.tmp`), macOS FSEvents/Spotlight metadata.
        # **Important**: do NOT prune `.shortcut-targets-by-id` — that's
        # exactly where Google Drive shortcut targets live; pruning it makes
        # shortcut-linked vaults invisible. Operator surfaced this during
        # v4.5.1 task 4 smoke testing.
        local found
        found="$($_timeout_cmd find -L "$probe_root" -maxdepth 5 \
            \( -name '.Trash*' \
               -o -name '.tmp' \
               -o -name '.fseventsd' \
               -o -name '.Spotlight-V100' \
            \) -prune \
            -o \
            \( -path '*/_meta/repos.json' -o -path '*/.obsidian' \) \
            -print 2>/dev/null | head -20 || true)"
        # v4.5.2 fix: rank + refine the markers via scripts/vault_probe.py
        # (stdlib, unit-tested) instead of inline dirname math. This keeps the
        # find SHALLOW (no deeper `-L` traversal, which risks symlink-loop hangs
        # when no `timeout` binary is installed) while still recovering a
        # MemoryVault nested inside an Obsidian app-vault:
        #   - --rank: repos.json roots win over .obsidian; an .obsidian root that
        #     is an ANCESTOR of a repos root is suppressed (it's the wrapper).
        #   - --refine: descend a candidate one level — if the root lacks the
        #     vault shape but exactly one child has it (e.g. .../Obsidian/AgentMemory),
        #     use that child. Recovers the deep-nested vault via its parent's
        #     shallow `.obsidian` hit.
        # Pre-v4.5.2 this picked the parent Obsidian app-vault over the nested
        # AgentMemory subfolder, splitting harness state across two roots.
        local ranked
        ranked="$(printf '%s\n' "$found" | python3 "$HARNESS_ROOT/scripts/vault_probe.py" --rank 2>/dev/null || true)"
        local cand_root refined
        while IFS= read -r cand_root; do
            [[ -z "$cand_root" ]] && continue
            refined="$(python3 "$HARNESS_ROOT/scripts/vault_probe.py" --refine "$cand_root" 2>/dev/null || echo "$cand_root")"
            [[ -z "$refined" ]] && refined="$cand_root"
            # De-dup
            local already=0
            local existing_c
            for existing_c in "${candidates[@]+"${candidates[@]}"}"; do
                [[ "$existing_c" == "$refined" ]] && already=1 && break
            done
            [[ $already -eq 0 ]] && candidates+=("$refined")
        done <<< "$ranked"
    fi

    if [[ ${#candidates[@]} -eq 0 ]]; then
        echo "    no Obsidian-vault candidates found under Google Drive."
        echo "    Set later via: python3 $HARNESS_ROOT/scripts/agentm_config.py --vault-path <path>"
        return 0
    fi

    echo "    candidates:"
    local i=1
    local c
    for c in "${candidates[@]}"; do
        echo "      $i) $c"
        i=$((i + 1))
    done
    echo "      m) enter manually"
    echo "      s) skip (set later via agentm_config.py)"
    # Read from /dev/tty so this works under `bash install.sh ...` pipes too.
    local choice=""
    if [[ -t 0 || -e /dev/tty ]]; then
        printf "    pick [1-%d / m / s]: " "${#candidates[@]}"
        read -r choice </dev/tty 2>/dev/null || choice="s"
    else
        echo "    (non-interactive; skipping vault prompt)"
        return 0
    fi

    local chosen_path=""
    case "$choice" in
        s|S|"")
            echo "    skipped; set later via agentm_config.py --vault-path"
            return 0
            ;;
        m|M)
            printf "    enter vault path: "
            read -r chosen_path </dev/tty 2>/dev/null || chosen_path=""
            ;;
        *)
            # Numeric selection
            if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#candidates[@]} )); then
                chosen_path="${candidates[$((choice - 1))]}"
            else
                echo "    invalid selection; skipping" >&2
                return 0
            fi
            ;;
    esac

    if [[ -z "$chosen_path" ]]; then
        echo "    no path entered; skipping"
        return 0
    fi
    # Hand off to agentm_config.py for validation + atomic write.
    if AGENTM_INSTALL_PREFIX="$prefix" python3 "$HARNESS_ROOT/scripts/agentm_config.py" \
            --vault-path "$chosen_path" 2>&1; then
        :  # success message printed by agentm_config.py
    else
        echo "    refused (see agentm_config.py message above); leaving vault_path unset" >&2
    fi
}

# ── --scope user: merge installed hooks' settings fragments (V4 #39) ─────────
# The --scope user install (symlink/copy) drops hook DIRS into
# <prefix>/hooks/<name>/ but, pre-v4.6.1, never merged their
# settings-fragment-bash.json into <prefix>/settings.json — so no SessionStart
# (or other) hook actually fired. This function walks the INSTALLED hook dirs
# (agentm harness/hooks/, landed under <prefix>/hooks/),
# merges each bash fragment, and ABSOLUTIZES the command to the user-scope dir
# layout `bash <prefix>/hooks/<name>/<name>.sh` (source fragments stay
# project-relative on disk; we rewrite per scope — locked DC-1). Writes a JSON
# array of {path, sha256} fragment records to $2 for the install-state
# `fragments` field (install-time metadata). Idempotent: re-running
# merges nothing new (dedup by absolutized command) + recomputes identical records.
_agentm_merge_user_hook_fragments() {
    local prefix="$1" out="$2"
    : > "$out.records"
    local hooks_dir="$prefix/hooks"
    if [[ ! -d "$hooks_dir" ]] || ! command -v python3 >/dev/null 2>&1; then
        printf '[]\n' > "$out"
        rm -f "$out.records"
        return 0
    fi
    local merged=0 hookdir name frag script sha
    for hookdir in "$hooks_dir"/*/; do
        [[ -d "$hookdir" ]] || continue
        name="$(basename "$hookdir")"
        frag="${hookdir}settings-fragment-bash.json"
        script="${hookdir}${name}.sh"
        # Only register hooks that ship a bash fragment AND a runnable script
        # (follows symlinks under source mode).
        [[ -f "$frag" && -f "$script" ]] || continue
        if python3 "$HARNESS_ROOT/scripts/merge-settings-fragment.py" \
                "$prefix/settings.json" "$frag" --command "bash $script" >/dev/null 2>&1; then
            merged=$((merged + 1))
            sha="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$frag" 2>/dev/null || echo "")"
            printf '%s\t%s\n' "$frag" "$sha" >> "$out.records"
        else
            echo "    WARN: failed to merge settings fragment for user-scope hook '$name'" >&2
        fi
    done
    # Arrayify the tab-separated records into the fragments JSON file.
    python3 -c "
import json, sys
recs = []
try:
    for line in open(sys.argv[1], encoding='utf-8'):
        line = line.rstrip('\n')
        if not line:
            continue
        path, _, sha = line.partition('\t')
        recs.append({'path': path, 'sha256': sha})
except FileNotFoundError:
    pass
with open(sys.argv[2], 'w', encoding='utf-8') as fh:
    json.dump(recs, fh, indent=2)
" "$out.records" "$out" 2>/dev/null || printf '[]\n' > "$out"
    rm -f "$out.records"
    echo "    hooks: merged $merged settings fragment(s) into $prefix/settings.json"
}

# ── --scope user dispatch (V4 #30 task 8) ───────────────────────────────────
# When --scope user, install customizations into ~/.claude/ via the symlink
# (source mode) or copy (release mode) primitive. Skip the per-project
# install flow entirely (V4 #26 moved state to vault; under user scope,
# nothing per-project is created).

if [[ "$SCOPE" == "user" ]]; then
  USER_INSTALL_PREFIX="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
  mkdir -p "$USER_INSTALL_PREFIX"
  echo "==> installing agentm (--scope user) into: $USER_INSTALL_PREFIX (version $HARNESS_VERSION)"

  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: --scope user requires python3 on PATH" >&2
    exit 1
  fi

  # Detect install mode (source vs release)
  DETECT_JSON="$(python3 "$HARNESS_ROOT/lib/install/python/install_state.py" detect 2>/dev/null || echo '{"mode":"release","source_clones":{}}')"
  MODE="$(echo "$DETECT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode', 'release'))")"
  echo "    install mode: $MODE"

  if [[ "$MODE" == "source" ]]; then
    # Source-mode: symlink customizations subset from source clones
    SOURCE_FLAGS=()
    [[ -d "$HOME/Antigravity/agentm" ]] && SOURCE_FLAGS+=(--agentm "$HOME/Antigravity/agentm")
    if [[ ${#SOURCE_FLAGS[@]} -gt 0 ]]; then
      _SYM_OUT="$(python3 "$HARNESS_ROOT/lib/install/python/install_symlinks.py" \
        "$USER_INSTALL_PREFIX" "${SOURCE_FLAGS[@]}" 2>/dev/null || echo '{}')"
      _SYM_REAPED="$(printf '%s' "$_SYM_OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); v=d.get('reaped') or []; print('\n'.join(v))" 2>/dev/null || true)"
      if [[ -n "$_SYM_REAPED" ]]; then
        _SYM_REAP_COUNT="$(printf '%s\n' "$_SYM_REAPED" | wc -l | tr -d ' ')"
        echo "    symlinks: created (reaped $_SYM_REAP_COUNT orphan(s):"
        printf '%s\n' "$_SYM_REAPED" | sed 's/^/      - /'
        echo "    )"
      else
        echo "    symlinks: created"
      fi
    fi
  else
    # Release-mode: copy customizations from this harness's source tree
    # (the operator who ran install.sh has the source available right here).
    # Walk relevant dirs.
    for src_subdir in harness/agents harness/skills harness/hooks adapters/claude-code; do
      if [[ -d "$HARNESS_ROOT/$src_subdir" ]]; then
        python3 "$HARNESS_ROOT/lib/install/python/install_copy.py" \
          "$HARNESS_ROOT/$src_subdir" "$USER_INSTALL_PREFIX" >/dev/null 2>&1 || true
      fi
    done
    # User-scope helper scripts. telemetry.sh roots across multiple projects
    # (`--all` scans ~/Antigravity etc.) so it belongs at <prefix>/scripts/,
    # not per-project. Mirrors install_symlinks.py source-mode behavior.
    if [[ -f "$HARNESS_ROOT/templates/scripts/telemetry.sh" ]]; then
      mkdir -p "$USER_INSTALL_PREFIX/scripts"
      cp "$HARNESS_ROOT/templates/scripts/telemetry.sh" "$USER_INSTALL_PREFIX/scripts/telemetry.sh"
      chmod +x "$USER_INSTALL_PREFIX/scripts/telemetry.sh"
    fi
    echo "    customizations: copied"
  fi

  # V4 #39: merge installed hooks' settings fragments into <prefix>/settings.json
  # (the pre-v4.6.1 gap — hook dirs landed but nothing fired). Produces a
  # {path, sha256} records file consumed by persist's --fragments-file below.
  _AGENTM_FRAG_RECORDS="$(mktemp -t agentm-frag.XXXXXX)"
  _agentm_merge_user_hook_fragments "$USER_INSTALL_PREFIX" "$_AGENTM_FRAG_RECORDS"

  # Persist install state (incl. the merged-fragments records for drift detection)
  python3 "$HARNESS_ROOT/lib/install/python/install_state.py" persist \
    "$USER_INSTALL_PREFIX" \
    --harness-version "$HARNESS_VERSION" \
    --installer-source "$HARNESS_ROOT/install.sh" \
    --fragments-file "$_AGENTM_FRAG_RECORDS" \
    "${PERSIST_STATE_MODE_ARGS[@]+"${PERSIST_STATE_MODE_ARGS[@]}"}" > /dev/null
  rm -f "$_AGENTM_FRAG_RECORDS"

  # Install agentm-update launcher to ~/.local/bin (if writable)
  USER_BIN="${HOME}/.local/bin"
  mkdir -p "$USER_BIN"
  if [[ -f "$HARNESS_ROOT/templates/bin/agentm-update" ]]; then
    cp "$HARNESS_ROOT/templates/bin/agentm-update" "$USER_BIN/agentm-update"
    chmod +x "$USER_BIN/agentm-update"
    echo "    launcher: $USER_BIN/agentm-update (add ~/.local/bin to PATH if not already)"
  fi

  # v4.5.1 task 4 — first-run vault detection (idempotent; --force-vault-prompt
  # re-fires when set; CI + non-Darwin auto-skip with one-line notice).
  # --local-state opts out of the vault entirely (Hardening I #44 task 4).
  if [[ $LOCAL_STATE -eq 1 ]]; then
    echo "    state_mode: local (repo-local, vault-less); skipping vault detection"
  else
    _agentm_vault_first_run_prompt "$USER_INSTALL_PREFIX"
  fi

  # Antigravity GLOBAL rules (V4 #22 Task 4b) — the user-scope Antigravity channel,
  # parity with ~/.claude/ for Claude Code. Merge the AgentMemory vault-usage
  # payload into ~/.gemini/GEMINI.md (Antigravity's global rules file, applied
  # across every workspace) as a managed section, so Antigravity picks up the
  # vault everywhere without a per-project install. Only when ~/.gemini/ already
  # exists (the operator runs Antigravity/Gemini) — we don't create config dirs
  # for tools they don't use. Idempotent; preserves the operator's own GEMINI.md.
  # Source = the Antigravity workspace rule body (read-write working-agent
  # framing); ONLY agentmemory-context goes global — harness.md is a per-project
  # operating contract, not a global rule.
  if [[ -d "$HOME/.gemini" ]]; then
    _agentmemory_src="$HARNESS_ROOT/adapters/antigravity/rules/agentmemory-context.md"
    if [[ -f "$_agentmemory_src" ]]; then
      echo "    Antigravity global rules → ~/.gemini/GEMINI.md"
      python3 "$HARNESS_ROOT/scripts/merge-managed-section.py" \
        "$HOME/.gemini/GEMINI.md" "$_agentmemory_src" \
        --marker AGENTMEMORY --strip-frontmatter \
        || echo "    WARN: failed to merge agentmemory-context into ~/.gemini/GEMINI.md (continuing)" >&2
    fi
  fi

  echo "==> done (--scope user)"
  exit 0
fi

# ── --scope project (default; legacy per-project install) ───────────────────
# Existing per-project install flow continues unchanged below.

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
  .claude/hooks      # compound hooks (memory-*, session-start) — agentm-native
                     # in harness/hooks/, dispatched here via the manifest
                     # dispatcher.
  .agent/rules       # legacy (pre-V4 #22) — wiped on --update; migrated to .agents/
  .agent/workflows   # legacy (pre-V4 #22) — wiped on --update; migrated to .agents/
  .agent/skills      # legacy (pre-V4 #22) — wiped on --update; migrated to .agents/
  .agents/rules
  .agents/workflows
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
  .agent
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
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".claude/commands/$(basename "$f")"
done
for f in "$HARNESS_ROOT"/adapters/claude-code/agents/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".claude/agents/$(basename "$f")"
done
for d in "$HARNESS_ROOT"/adapters/claude-code/skills/*/; do
  [[ -d "$d" ]] || continue
  cp_managed_dir "$d" ".claude/skills/$(basename "$d")"
done

# .agents/ — Antigravity config (full-parity adapter). Copies the always-on
# rules files into the target's .agents/ tree; the shared skills land in
# .agents/skills/ via the compound dispatcher + shared-skills delivery below.
# V4 #22: migrated .agent/ (singular) → .agents/ (plural) — the Antigravity 2.0
# default per the official rules-workflows docs ("Antigravity now defaults to
# .agents/rules"). Legacy .agent/ is wiped on --update (see MANAGED_PARENTS +
# EMPTY_PARENT_CANDIDATES above). Post-V5 dev-loop slim, agentm ships no
# Antigravity workflows (the dev loop moved to crickets); .agents/workflows
# stays in MANAGED_PARENTS so a pre-slim install's stale workflows are wiped on
# --update.
mkdir -p .agents/rules .agents/skills
for f in "$HARNESS_ROOT"/adapters/antigravity/rules/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".agents/rules/$(basename "$f")"
done

# .agents/skills/ — shared skills delivery (read by BOTH Gemini CLI and
# Antigravity per the Agent Skills standard; V4 #22 migrated the Antigravity
# adapter onto .agents/, so it now reads this shared path too). Source:
# adapters/claude-code/skills/ — the canonical home for the shared skills.
# The `doctor` skill here is host-aware (detects claude-code / antigravity /
# gemini at runtime). The antigravity + gemini adapters deliberately do NOT
# duplicate these (deduped — see check-parity), so they're delivered once here.
mkdir -p .agents/skills
for name in doctor; do
  src="$HARNESS_ROOT/adapters/claude-code/skills/$name"
  [[ -d "$src" ]] || continue
  cp_managed_dir "$src" ".agents/skills/$name"
done

# .gemini/ — Gemini CLI config. Post-V5 dev-loop slim, agentm ships no Gemini
# phase commands or sub-agents (the dev loop moved to crickets). Gemini's
# surface is just settings.json (AGENTS.md context.fileName wiring) plus the
# shared skills delivered to .agents/skills/ above (read natively per the Agent
# Skills standard). settings.json uses cp_user semantics (never clobber existing
# user config — README documents the AGENTS.md fileName merge if they already
# have a settings.json). The .gemini/commands + .gemini/agents dirs stay in
# MANAGED_PARENTS so a pre-slim install's stale dev-loop files are wiped on --update.
mkdir -p .gemini
cp_user "$HARNESS_ROOT/adapters/gemini/settings.json" ".gemini/settings.json"

# ── wiki/ — documentation scaffold (per-file walk, skip-if-exists) ──────────
# Source: $HARNESS_ROOT/templates/wiki/ (NOT $HARNESS_ROOT/wiki/ — that's
# this repo's own dogfooded docs and never ships to targets).
cp_user_walk "$HARNESS_ROOT/templates/wiki" "wiki"

# ── compound customizations (V4 #36) ───────────────────────────────────────
#
# Walks harness/skills/<dir>/SKILL.md, harness/hooks/<dir>/hook.md, and
# harness/agents/<file>.md, dispatching each based on its supported_hosts
# field. These compound customizations are agentm-native (in harness/) —
# originally imported from crickets in v4.0.0 (plan #18 DC #28) and since
# owned by agentm: the memory / design / ship-release skills, the memory +
# session hooks (memory-recall-*, memory-reflect-*, conflict-merger-session-start,
# harness-context-session-start), and the memory-idea-researcher +
# adapt-evaluator sub-agents.
#
# Only dispatches entries with crickets-shape frontmatter (kind: <type> +
# supported_hosts: <list>). The legacy agentm single-file skill (doctor.md)
# at harness/skills/*.md without frontmatter flows through the adapters/
# pipeline above and is skipped here.

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
                # Copy sibling .py helpers (a hook may ship a .py
                # alongside its .sh).
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
  python3 "$HARNESS_ROOT/lib/install/python/install_state.py" persist \
    .claude \
    --harness-version "$HARNESS_VERSION" \
    --installer-source "$HARNESS_ROOT/install.sh" \
    "${PERSIST_STATE_MODE_ARGS[@]+"${PERSIST_STATE_MODE_ARGS[@]}"}" >/dev/null 2>&1 || true
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
    echo "  3. Run /doctor (Claude Code) to verify the install"
  else
    echo "  2. Run /doctor (Claude Code) to verify the install"
  fi
fi
