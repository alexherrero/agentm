#!/usr/bin/env bash
# install.sh — install agentm machine-wide, into ~/.claude/.
#
# Usage:
#   /path/to/agentm/install.sh [--local-state] [--daemon|--no-daemon] [--no-embedder]
#
# There is one install scope: this machine. Customizations land in
# $AGENTM_INSTALL_PREFIX (default ~/.claude/) and are shared by every project
# on the host. The per-project install (`--scope project`) that copied a
# `.claude/` tree into each target repo is retired; so is `--update`, because
# re-running this installer IS the refresh — source-mode installs are symlinks
# that never go stale, and release-mode installs re-copy every run.
#
# Options:
#   --local-state  Opt this machine into repo-local (vault-less) harness state:
#              writes "state_mode": "local" to .agentm-config.json (the on-host
#              config; DC-8) and skips vault auto-detection. State then lives in
#              <repo>/.harness/ instead of a MemoryVault.
#   --daemon   Build the Go memory daemon and install it as a launchd agent
#              (macOS only) so it survives a reboot. Builds ~/.local/bin/agentmd
#              with CGO_ENABLED=0, writes ~/Library/LaunchAgents/
#              com.agentm.daemon.plist, loads AND starts it, and verifies it
#              answers on /health before returning. Loading is not starting: a
#              bootstrapped job whose spawn launchd has parked is a daemon that
#              never runs, so the start is issued explicitly rather than left
#              to RunAtLoad.
#
#              Only needed ONCE. After the agent exists, every install run
#              rebuilds and reloads it automatically, so a refresh of the
#              harness is also a refresh of the daemon — the binary is built
#              from daemon/, and stale source would otherwise keep running
#              indefinitely with nothing saying so.
#
#   --no-embedder
#              Skip fetching the embedding model. The daemon then runs
#              lexical-only: hybrid retrieval is unavailable and every status
#              surface says so, which is a working install rather than a broken
#              one. Use it on a machine that should not spend ~330MB of disk,
#              or where the model would be fetched over a metered link.
#
#   --no-daemon  Skip that automatic refresh for this run. The daemon keeps
#              running whatever binary it already has.
#
#   --mcp-server   RETIRED. Generated a launchd plist for the Python FastMCP
#              memory server, which was retired when the Go daemon took over
#              port 7821. The flag now refuses rather than installing a second
#              agent that would fight the real one for the port. Use --daemon.
#              the operator runs the commands. macOS only.
#
# Re-running is idempotent and is the supported way to refresh an install.

set -euo pipefail

# Installer boundary: this script copies ONLY from $HARNESS_ROOT/harness/,
# $HARNESS_ROOT/adapters/, and $HARNESS_ROOT/templates/. The top-level
# $HARNESS_ROOT/wiki/ tree is this repo's own dogfooded documentation (how to
# use the harness) and must NEVER be installed. Do not add copy paths that
# read from $HARNESS_ROOT/wiki/.
HARNESS_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
HARNESS_VERSION="$(git -C "$HARNESS_ROOT" describe --tags --abbrev=0 2>/dev/null || echo "dev")"

FORCE_VAULT_PROMPT=0   # v4.5.1 task 4: re-fire first-run vault prompt
LOCAL_STATE=0          # Hardening I #44 task 4: --local-state → repo-local (vault-less) state
INSTALL_MCP_SERVER=0   # RETIRED — refuses; the Python server it served is gone
INSTALL_DAEMON=0       # --daemon → build the Go daemon + install the launchd agent
NO_DAEMON=0            # --no-daemon → skip the automatic refresh of an installed daemon
NO_EMBEDDER=0          # --no-embedder → do not fetch the embedding model; run lexical-only

# Retired flags. Each names its replacement rather than being silently ignored:
# an operator (or a stale script) passing one is telling us it expects behavior
# this installer no longer has, and a quiet no-op would let that expectation
# ride until something downstream broke instead.
_retired_flag() {
  echo "Error: $1 is retired — $2" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  arg="$1"
  case "$arg" in
    --force-vault-prompt) FORCE_VAULT_PROMPT=1; shift ;;
    --local-state) LOCAL_STATE=1; shift ;;
    --mcp-server) INSTALL_MCP_SERVER=1; shift ;;
    --daemon) INSTALL_DAEMON=1; shift ;;
    --no-daemon) NO_DAEMON=1; shift ;;
    --no-embedder) NO_EMBEDDER=1; shift ;;
    --scope|--scope=*)
      _retired_flag "--scope" \
        "there is one install scope now (this machine, \$AGENTM_INSTALL_PREFIX, default ~/.claude/). Drop the flag."
      ;;
    --update)
      _retired_flag "--update" \
        "re-running this installer with no flags IS the refresh. Drop the flag."
      ;;
    --hooks)
      _retired_flag "--hooks" \
        "harness hooks install automatically now. The per-project verify/precompact hooks it used to install are being re-homed machine-wide; see PLAN-user-scope-hook-gaps."
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
      echo "Error: unexpected argument: $arg" >&2
      echo "  This installer takes no target path — it installs machine-wide to" >&2
      echo "  \${AGENTM_INSTALL_PREFIX:-\$HOME/.claude}. The per-project install is retired." >&2
      exit 1
      ;;
  esac
done

# Hardening I #44 task 4: --local-state threads `--state-mode local` into the
# install-state persist call, so .agentm-config.json becomes the on-host source
# of truth for repo-local, vault-less harness state (DC-8). Empty array when
# not set; every expansion
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
# via agentm_config.py. Triggers when vault_path is unset OR
# --force-vault-prompt is passed. CI-skipped via $CI=true env.
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

# ── merge installed hooks' settings fragments (V4 #39) ──────────────────────
# The install (symlink/copy) drops hook DIRS into
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

# ── install dispatch ────────────────────────────────────────────────────────
# Install customizations into $AGENTM_INSTALL_PREFIX (default ~/.claude/) via
# the symlink (source mode) or copy (release mode) primitive. Nothing
# per-project is created: state lives in the vault (V4 #26), and the
# per-project install flow is retired.

USER_INSTALL_PREFIX="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
mkdir -p "$USER_INSTALL_PREFIX"
echo "==> installing agentm into: $USER_INSTALL_PREFIX (version $HARNESS_VERSION)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: agentm requires python3 on PATH" >&2
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
  # harness/{agents,skills,hooks} each need their own name as the
  # destination's top-level dir (install_copy.py relativizes against
  # source_dir, so copying straight into $USER_INSTALL_PREFIX drops that
  # segment entirely — mirrors install_symlinks.py's explicit
  # "agents/{name}" / "skills/{name}" / "hooks/{name}" destination
  # mapping, the source-mode reference this release-mode path must match).
  for src_subdir in harness/agents harness/skills harness/hooks; do
    if [[ -d "$HARNESS_ROOT/$src_subdir" ]]; then
      python3 "$HARNESS_ROOT/lib/install/python/install_copy.py" \
        "$HARNESS_ROOT/$src_subdir" "$USER_INSTALL_PREFIX/$(basename "$src_subdir")" >/dev/null 2>&1 || true
    fi
  done
  # adapters/claude-code already nests commands/, skills/, agents/ as its
  # own immediate children — copying it straight into the prefix is correct
  # as-is, unlike the harness/* trio above.
  if [[ -d "$HARNESS_ROOT/adapters/claude-code" ]]; then
    python3 "$HARNESS_ROOT/lib/install/python/install_copy.py" \
      "$HARNESS_ROOT/adapters/claude-code" "$USER_INSTALL_PREFIX" >/dev/null 2>&1 || true
  fi
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

# ── --mcp-server: retired ───────────────────────────────────────────────────
# The Python FastMCP memory server this generated a plist for was retired when
# the Go daemon took over port 7821. Generating the plist anyway would install a
# second launchd agent that loses a race for the port and then retries forever,
# so the flag refuses and names its replacement instead of failing later in a
# way nobody would connect back to this decision.

if [[ $INSTALL_MCP_SERVER -eq 1 ]]; then
  echo "" >&2
  echo "==> --mcp-server is retired." >&2
  echo "    It installed the Python FastMCP memory server, which was retired when" >&2
  echo "    the Go daemon took over port 7821. Installing it now would put two" >&2
  echo "    agents on the same port." >&2
  echo "" >&2
  echo "    Use --daemon instead: it builds the Go daemon and installs it as a" >&2
  echo "    launchd agent that survives a reboot." >&2
  echo "" >&2
  exit 2
fi

# ── the Go memory daemon: install on request, refresh automatically ─────────
# Two modes, one code path.
#
#   install  — --daemon was passed. The operator is asking for the launchd agent,
#              so a missing toolchain or a failed build is a hard error.
#   refresh  — the agent already exists, and this is any ordinary install or
#              --update run. The binary is compiled from daemon/, so refreshing
#              the harness without rebuilding it leaves stale code resident with
#              nothing saying so. This is the case that makes a reinstall mean
#              what people assume it means.
#
# Refresh is deliberately non-fatal. A project install must not fail because Go
# was uninstalled, and a broken build must never take down a daemon that is
# currently working — so the build goes to a sibling path and only replaces the
# live binary once it has succeeded.

DAEMON_LABEL="com.agentm.daemon"
DAEMON_PLIST="$HOME/Library/LaunchAgents/$DAEMON_LABEL.plist"
# Overridable so the installer's own tests can drive this path against a stub
# instead of the machine's real launchd. The reload sequence below broke in
# production once; it is worth being able to test.
LAUNCHCTL="${AGENTM_LAUNCHCTL:-launchctl}"

DAEMON_MODE=none
if [[ $INSTALL_DAEMON -eq 1 ]]; then
  DAEMON_MODE=install
elif [[ $NO_DAEMON -eq 0 && -f "$DAEMON_PLIST" ]]; then
  DAEMON_MODE=refresh
fi

if [[ "$DAEMON_MODE" != "none" && "$(uname -s)" != "Darwin" ]]; then
  if [[ "$DAEMON_MODE" == "install" ]]; then
    echo "==> --daemon: skipped (launchd is macOS-only; on this OS run 'agentmd serve' under your service manager)." >&2
  fi
  DAEMON_MODE=none
fi

if [[ "$DAEMON_MODE" != "none" ]]; then
  # daemon_fail <message> [<state-line>…] — hard error when installing, loud
  # warning when refreshing. Never silent in either case.
  #
  # The lines after the message say what the operator is left with, and they
  # are arguments rather than a fixed sentence because the two classes of
  # failure leave opposite states behind. A missing toolchain or a failed build
  # happens before the reload, so the old daemon is untouched and still
  # serving. A reload failure happens after the bootout, so there is no daemon
  # at all. One constant claiming "it keeps running whatever binary it already
  # has" covered both, and was false in exactly the case that mattered: the
  # machine left with no memory daemon, which is every recall returning
  # nothing, reported as a caveat on an otherwise successful install.
  daemon_fail() {
    local msg="$1"; shift
    local line
    if [[ "$DAEMON_MODE" == "install" ]]; then
      echo "Error: --daemon: $msg" >&2
      for line in "$@"; do echo "    $line" >&2; done
      exit 1
    fi
    echo "==> WARNING: the memory daemon was NOT refreshed: $msg" >&2
    for line in "$@"; do echo "    $line" >&2; done
    echo "    Re-run with --daemon once the cause is fixed." >&2
    DAEMON_MODE=none
  }

  # daemon_reload_fail <message> <state-line>… — the post-bootout sibling of
  # daemon_fail. Same install-is-fatal / refresh-is-loud split, different
  # headline: by the time these fire the binary HAS been rebuilt and the old
  # agent HAS been booted out, so "was NOT refreshed" tells the wrong story.
  # What failed is bringing the daemon back up, and the caller measures the
  # state it is reporting instead of asserting one.
  daemon_reload_fail() {
    local msg="$1"; shift
    local line
    if [[ "$DAEMON_MODE" == "install" ]]; then
      echo "Error: --daemon: $msg" >&2
      for line in "$@"; do echo "    $line" >&2; done
      exit 1
    fi
    echo "==> WARNING: the memory daemon did not come back up: $msg" >&2
    for line in "$@"; do echo "    $line" >&2; done
    DAEMON_MODE=none
  }

  # The residual state for every failure that happens before the reload:
  # nothing has been stopped yet, so the resident daemon is still the old one.
  DAEMON_STILL_RESIDENT="It keeps running whatever binary it already has, which may now be older than daemon/."

  DAEMON_SRC="$HARNESS_ROOT/daemon"
  DAEMON_BIN_DIR="$HOME/.local/bin"
  DAEMON_BIN="$DAEMON_BIN_DIR/agentmd"
  DAEMON_LOG_DIR="$HOME/Library/Logs/agentm"

  if [[ ! -d "$DAEMON_SRC" ]]; then
    daemon_fail "no daemon/ directory at $DAEMON_SRC" "$DAEMON_STILL_RESIDENT"
  fi
fi

if [[ "$DAEMON_MODE" != "none" ]] && ! command -v go >/dev/null 2>&1; then
  daemon_fail "Go is not installed, and the daemon is built from source rather than vendored (fix: brew install go)" \
    "$DAEMON_STILL_RESIDENT"
fi

if [[ "$DAEMON_MODE" != "none" ]]; then
  mkdir -p "$DAEMON_BIN_DIR" "$DAEMON_LOG_DIR" "$HOME/Library/LaunchAgents"

  if [[ "$DAEMON_MODE" == "refresh" ]]; then
    echo "==> Refreshing the memory daemon from daemon/ …"
  else
    echo "==> Building the memory daemon (CGO_ENABLED=0, static, no cgo)…"
  fi

  # Build beside the live binary, then swap. A failed build leaves the running
  # daemon untouched; the rename is atomic, and replacing the file does not
  # disturb the running process, which keeps its own inode until reload.
  if ( cd "$DAEMON_SRC" && CGO_ENABLED=0 go build -o "$DAEMON_BIN.new" ./cmd/agentmd ); then
    mv -f "$DAEMON_BIN.new" "$DAEMON_BIN"
    echo "    built $DAEMON_BIN"
  else
    rm -f "$DAEMON_BIN.new"
    daemon_fail "the daemon build failed; the existing binary was left in place" "$DAEMON_STILL_RESIDENT"
  fi
  # The dreaming binary (filing v2 part 6): one pass and exit, driven by the
  # runner's job manifest (templates/jobs/dreaming.yaml), never resident — so
  # no launchd entry of its own. Built beside the daemon, swapped the same way.
  DREAMER_BIN="$DAEMON_BIN_DIR/agentmdream"
  if ( cd "$DAEMON_SRC" && CGO_ENABLED=0 go build -o "$DREAMER_BIN.new" ./cmd/agentmdream ); then
    mv -f "$DREAMER_BIN.new" "$DREAMER_BIN"
    echo "    built $DREAMER_BIN"
  else
    rm -f "$DREAMER_BIN.new"
    echo "    warning: the dreaming binary build failed; the existing one (if any) was left in place" >&2
  fi
fi

# ── the embedding model: fetched once, verified by checksum ─────────────────
# The daemon runs models as supervised children, so the weights are data the
# install fetches rather than code it builds. One model, pinned by SHA-256.
#
# A checksum mismatch deletes the file and fails rather than keeping it. A GGUF
# that is subtly not what we pinned produces vectors that are merely wrong —
# every search still answers, just worse — which is the failure mode nobody
# notices for months.
#
# Never fatal in refresh mode, and never fatal at all when the fetch is what
# fails: an install without the model is the lexical-only daemon, which is a
# supported configuration that says so on every status surface.

MODEL_DIR="$HOME/.local/share/agentm/models"
# Pinned by the bake-off in the hybrid-retrieval plan, task 2. EmbeddingGemma
# won on the frozen goldv2 corpus; the hash is the file that was measured, not
# one copied from a model card.
MODEL_FILE="embeddinggemma-300M-Q8_0.gguf"
MODEL_SHA="b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63"
MODEL_URL="https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/$MODEL_FILE"

if [[ "$DAEMON_MODE" != "none" && $NO_EMBEDDER -eq 0 ]]; then
  model_sha256() {
    if command -v shasum >/dev/null 2>&1; then
      shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
      sha256sum "$1" | awk '{print $1}'
    else
      echo ""
    fi
  }

  if [[ -f "$MODEL_DIR/$MODEL_FILE" ]] && [[ "$(model_sha256 "$MODEL_DIR/$MODEL_FILE")" == "$MODEL_SHA" ]]; then
    echo "    embedding model already present and verified ($MODEL_FILE)"
  elif ! command -v curl >/dev/null 2>&1; then
    echo "==> NOTE: curl is missing, so the embedding model was not fetched." >&2
    echo "    The daemon runs lexical-only until it is. Hybrid retrieval is off." >&2
  else
    mkdir -p "$MODEL_DIR"
    echo "==> Fetching the embedding model (~330MB, one time)…"
    # To a temp path, then verified, then moved. A half-downloaded GGUF at the
    # real path would be loaded on the next start and fail as a model bug.
    if curl -fsSL --retry 2 -o "$MODEL_DIR/$MODEL_FILE.part" "$MODEL_URL"; then
      GOT="$(model_sha256 "$MODEL_DIR/$MODEL_FILE.part")"
      if [[ -z "$GOT" ]]; then
        echo "==> NOTE: no sha256 tool available, so the model could not be verified." >&2
        echo "    Not installing it — an unverified model is worse than none." >&2
        rm -f "$MODEL_DIR/$MODEL_FILE.part"
      elif [[ "$GOT" == "$MODEL_SHA" ]]; then
        mv -f "$MODEL_DIR/$MODEL_FILE.part" "$MODEL_DIR/$MODEL_FILE"
        echo "    verified and installed $MODEL_DIR/$MODEL_FILE"
      else
        rm -f "$MODEL_DIR/$MODEL_FILE.part"
        echo "==> WARNING: the embedding model failed its checksum and was discarded." >&2
        echo "    expected $MODEL_SHA" >&2
        echo "    got      $GOT" >&2
        echo "    The daemon runs lexical-only. Re-run to retry." >&2
      fi
    else
      echo "==> NOTE: the embedding model could not be downloaded." >&2
      echo "    The daemon runs lexical-only until it is; re-run to retry." >&2
    fi
  fi

  # llama-server is the runtime the weights need. It is not built here — it is a
  # cgo project, and building it is exactly what the daemon's static pure-Go
  # constraint exists to avoid — so its absence is reported, not repaired.
  if ! command -v llama-server >/dev/null 2>&1; then
    echo "==> NOTE: llama-server is not on PATH, so hybrid retrieval stays off." >&2
    echo "    Install it (macOS: brew install llama.cpp) and the daemon picks it up" >&2
    echo "    on its next start. Until then every status surface reports the" >&2
    echo "    embedder as off and searches run lexical-only." >&2
  fi
elif [[ "$DAEMON_MODE" != "none" && $NO_EMBEDDER -eq 1 ]]; then
  echo "==> --no-embedder: skipping the embedding model. The daemon runs lexical-only."
fi

if [[ "$DAEMON_MODE" != "none" ]]; then
  # PATH is set explicitly because a launchd agent inherits almost nothing.
  cat > "$DAEMON_PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$DAEMON_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$DAEMON_BIN</string>
        <string>serve</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$DAEMON_BIN_DIR</string>
    </dict>

    <!-- Resident by definition: start at login, restart if it dies. The vault
         path is resolved from the kernel config at every start, never baked in
         here — a path in this file would be a cached literal that goes stale. -->
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Standard</string>
    <!-- Bounds the retry rate if the binary is missing or the port is taken,
         so a broken install idles instead of spinning. -->
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$DAEMON_LOG_DIR/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>$DAEMON_LOG_DIR/daemon.log</string>
</dict>
</plist>
PLISTEOF

  # Reload, so re-running after a source pull is the same command as installing.
  #
  # bootout returns before the job is actually gone, and bootstrapping into a
  # domain that still holds the old job fails. Caught on the first real reload:
  # the build succeeded, bootstrap failed, and the daemon was left down. So wait
  # for the unload to land, then retry the bootstrap rather than treating one
  # attempt as the answer.
  if "$LAUNCHCTL" print "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1; then
    "$LAUNCHCTL" bootout "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
      "$LAUNCHCTL" print "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1 || break
      sleep 1
    done
  fi
  DAEMON_LOADED=0
  for _ in $(seq 1 5); do
    if "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$DAEMON_PLIST" 2>/dev/null; then
      DAEMON_LOADED=1; break
    fi
    # Already loaded is success, not failure — a concurrent load beat us to it.
    if "$LAUNCHCTL" print "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1; then
      DAEMON_LOADED=1; break
    fi
    sleep 2
  done
  if [[ $DAEMON_LOADED -eq 0 ]]; then
    daemon_reload_fail "launchctl bootstrap failed for $DAEMON_PLIST" \
      "The old agent was booted out first, so this machine now has NO memory daemon." \
      "Recall's daemon path is the only one that fits its budget on a real vault," \
      "so until the agent loads again every recall comes back empty." \
      "Start it with: launchctl bootstrap gui/\$(id -u) $DAEMON_PLIST" \
      "Log:           $DAEMON_LOG_DIR/daemon.log"
  else
    # Loaded is not started. RunAtLoad asks launchd to spawn the job when it is
    # bootstrapped; it does not oblige launchd to do it now. When the gui/<uid>
    # domain is in on-demand-only mode the spawn is parked instead, and launchd
    # says so only in its own log:
    #
    #   [com.agentm.daemon:] This service is defined to be constantly running…
    #   [gui/501:] pending spawn, domain in on-demand-only mode: com.agentm.daemon
    #
    # bootstrap still returns success, and the label still appears in `launchctl
    # list` — with no pid beside it. KeepAlive does not rescue it either, because
    # there is no process to keep alive. Seen in production: a refresh booted the
    # old daemon out, bootstrapped the new job, waited 45 seconds for a /health
    # that could never come, and left the machine with no daemon at all.
    #
    # kickstart is the imperative form — start this now — and is a no-op
    # against a job that is already running, so it costs nothing on the runs
    # where launchd had already obliged. Its exit status is not the verdict;
    # the /health probe below is.
    "$LAUNCHCTL" kickstart "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1 || true
  fi
fi

if [[ "$DAEMON_MODE" != "none" ]]; then
  # Verify it is actually answering rather than merely loaded. A job that
  # launchd accepted and that then died on a held port looks identical to a
  # working one in 'launchctl list' — which is exactly how the retired daemon
  # stayed 'healthy' and wired to nothing for months.
  #
  # The port comes from the same kernel-config key the daemon itself reads
  # (daemon.port, resolved by internal/config), not from the default spelled
  # in again here. Resolving it rather than recalling it fixes two things a
  # fixed 7821 got wrong in opposite directions: an operator who moved the
  # port had a daemon that came up correctly and an install that failed 45
  # seconds later probing a port nothing was on; and a probe aimed at a
  # well-known port is answered by whatever holds it, which is how this check
  # reported a healthy daemon in the installer's own tests while the daemon it
  # claimed to verify had never been started.
  DAEMON_PORT=""
  if command -v python3 >/dev/null 2>&1; then
    DAEMON_PORT="$(python3 "$HARNESS_ROOT/scripts/agentm_config.py" --get daemon.port 2>/dev/null || true)"
  fi
  # Unset, unreadable, or not a number all mean the daemon will take its own
  # default, so probe that. Never a partially-parsed value.
  [[ "$DAEMON_PORT" =~ ^[0-9]+$ ]] || DAEMON_PORT=7821

  # Seconds to wait for /health. A seam for the same reason AGENTM_LAUNCHCTL is
  # one: the failure path below is the part that broke in production, and a test
  # that has to burn the real timeout to reach it is a test nobody runs. It
  # bounds the wait only — never which branch is taken, or what it reports.
  DAEMON_HEALTH_TIMEOUT="${AGENTM_DAEMON_HEALTH_TIMEOUT:-45}"

  # The pid launchd holds for the job, or empty when it is not running.
  # `launchctl print` emits a `pid = N` line only while a job is actually
  # running; a job that is loaded but has never spawned prints `state = not
  # running` and no pid at all, which is precisely the state this block exists
  # to stop reporting as a daemon that kept running.
  daemon_pid() {
    "$LAUNCHCTL" print "gui/$(id -u)/$DAEMON_LABEL" 2>/dev/null \
      | awk -F' = ' '$1 ~ /^[[:space:]]*pid$/ { print $2; exit }' || true
  }

  # Whoever is listening on DAEMON_PORT, as "cmd (pid N)", or empty when the
  # port is free. Measured, not assumed: the message this replaces blamed port
  # contention on every failure, and on the one that was reported nothing held
  # the port at all — so it sent the operator to look at the wrong thing.
  port_holder() {
    command -v lsof >/dev/null 2>&1 || return 0
    lsof -nP -iTCP:"$DAEMON_PORT" -sTCP:LISTEN 2>/dev/null \
      | awk 'NR > 1 { print $1 " (pid " $2 ")"; exit }' || true
  }

  daemon_health_probe() {
    local _i
    for _i in $(seq 1 "$1"); do
      if curl -fsS -m 2 "http://127.0.0.1:$DAEMON_PORT/health" >/dev/null 2>&1; then return 0; fi
      sleep 1
    done
    return 1
  }

  DAEMON_UP=0
  if daemon_health_probe "$DAEMON_HEALTH_TIMEOUT"; then
    DAEMON_UP=1
  elif [[ -z "$(daemon_pid)" ]]; then
    # Loaded but never spawned — the parked-spawn case again. The kickstart
    # above should have prevented it; this is a second attempt, not the first,
    # and -k forces a restart in case the job spawned and wedged in between.
    # Guarded on the job not running, so it can never kill a healthy daemon
    # that is merely slow to answer.
    echo "    /health did not answer in ${DAEMON_HEALTH_TIMEOUT}s and the job is not running — starting it…" >&2
    "$LAUNCHCTL" kickstart -k "gui/$(id -u)/$DAEMON_LABEL" >/dev/null 2>&1 || true
    if daemon_health_probe "$DAEMON_HEALTH_TIMEOUT"; then DAEMON_UP=1; fi
  fi
  if [[ $DAEMON_UP -eq 1 ]]; then
    if [[ "$DAEMON_MODE" == "refresh" ]]; then
      echo "    memory daemon refreshed and answering on http://127.0.0.1:$DAEMON_PORT"
    else
      echo "==> Memory daemon installed and answering on http://127.0.0.1:$DAEMON_PORT"
      echo "    It now starts at login, restarts if it dies, and refreshes on every"
      echo "    future install or --update run (opt out with --no-daemon)."
      echo "    Logs:      $DAEMON_LOG_DIR/daemon.log"
      echo "    Status:    agentmd status"
      echo "    Uninstall: launchctl bootout gui/\$(id -u)/$DAEMON_LABEL && rm $DAEMON_PLIST"
    fi
  else
    # What the operator is actually left with, measured here rather than
    # asserted. Three states are possible and they call for different words:
    # the daemon running but mute, something else holding the port, or — the
    # one that used to be reported as "it keeps running" — no daemon at all.
    DAEMON_PID_NOW="$(daemon_pid)"
    PORT_HOLDER_NOW="$(port_holder)"
    if [[ -n "$DAEMON_PID_NOW" ]]; then
      daemon_reload_fail "the daemon is running but did not answer /health within ${DAEMON_HEALTH_TIMEOUT}s" \
        "It is RUNNING as pid $DAEMON_PID_NOW and not serving. Recall will not use it," \
        "so until it answers, every recall comes back empty." \
        "Log: $DAEMON_LOG_DIR/daemon.log"
    elif [[ -n "$PORT_HOLDER_NOW" ]]; then
      daemon_reload_fail "the agent loaded but nothing answered /health within ${DAEMON_HEALTH_TIMEOUT}s" \
        "The daemon is now STOPPED, and port $DAEMON_PORT is held by $PORT_HOLDER_NOW," \
        "so it could not bind. Until that port is free and the daemon is up," \
        "every recall comes back empty." \
        "Free the port, then: launchctl kickstart -k gui/\$(id -u)/$DAEMON_LABEL" \
        "Log:                $DAEMON_LOG_DIR/daemon.log"
    else
      daemon_reload_fail "the agent loaded but nothing answered /health within ${DAEMON_HEALTH_TIMEOUT}s" \
        "The daemon is now STOPPED: no process is running and nothing is listening" \
        "on port $DAEMON_PORT. Until it is started, every recall comes back empty." \
        "Start it with: launchctl kickstart -k gui/\$(id -u)/$DAEMON_LABEL" \
        "Log:           $DAEMON_LOG_DIR/daemon.log"
    fi
  fi
fi
# ── done ────────────────────────────────────────────────────────────────────

echo ""
echo "==> done (agentm $HARNESS_VERSION installed to $USER_INSTALL_PREFIX)."
echo ""
echo "Next steps:"
echo "  1. Run /doctor (Claude Code) to verify the install"
echo "  2. Re-run this installer any time to refresh; it is idempotent"
