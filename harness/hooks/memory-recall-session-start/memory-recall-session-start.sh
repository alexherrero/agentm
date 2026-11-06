#!/usr/bin/env bash
# memory-recall-session-start — load MemoryVault always-load entries on session boot.
#
# Fires on Claude Code's SessionStart event. Calls the memory skill's recall.py
# helper to glob _always-load/*.md entries + emit their bodies on stdout (which
# Claude Code injects as additional session context). Transparency line on
# stderr. Hard 500ms time budget; degraded-graceful on overrun.
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block session boot.

# The payload is read once, here. This hook wrote a `.harness/session-id-<sid>.start`
# marker for the idle hook's orphan sweep until task 199, which moved mining to
# the night (lesson_compile.py, keyed by a per-session bookmark in the engine's
# state directory); nothing reads a marker any more, so none is written.
PAYLOAD="$(cat 2>/dev/null || true)"

# ── Which host fired this? (Antigravity registration, 2026-10-30) ───────────
# The briefing below is Claude Code's. It records that it was shown, so a
# session that only injects it into a model (an `agy -p` run nobody reads)
# would use up the operator's one showing.
#
# Two independent signals, either of which skips it. The registration for a
# non-Claude host sets AGENTM_HOST (anything but claude-code, or unset, which is
# every Claude Code registration that exists). And a transcript under a .gemini
# directory is never Claude's, whatever the environment says, so a registration
# that lost the variable still cannot use up the showing.
HOST_IS_CLAUDE=1
case "${AGENTM_HOST:-claude-code}" in
    claude-code) ;;
    *) HOST_IS_CLAUDE=0 ;;
esac
if [[ "$HOST_IS_CLAUDE" == 1 && -n "$PAYLOAD" ]]; then
    case "$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    print((json.loads(sys.stdin.read()) or {}).get("transcript_path") or "")
except Exception:
    pass
' 2>/dev/null)" in
        */.gemini/*) HOST_IS_CLAUDE=0 ;;
    esac
fi

# ── Recall pass ────────────────────────────────────────────────────────────
# Resolve recall.py across install scopes. Memory skill is agentm-native
# (harness/skills/memory/). Install can be project-scope
# (<cwd>/.claude/skills/memory/...), user-scope (~/.claude/skills/memory/...,
# default since agentm v4.3.0 — a symlink into the agentm clone under source
# mode), or a source-mode clone fallback (path recorded in .agentm-config.json's
# source_clones.agentm → <clone>/harness/skills/memory/). Pre-fix, this hook
# hardcoded the project-scope relative path and silently no-op'd on user-scope.
_resolve_memory_script() {  # $1 = script basename (e.g. recall.py)
    local script="$1"
    local prefix="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
    # The project-scope copy is looked for under the cwd only on Claude Code. On
    # another host (AGENTM_HOST, set by the Antigravity registration) the cwd is
    # whatever workspace was opened, and agy does not ask whether to trust a
    # workspace before it runs a plugin's hooks: a cloned repo shipping its own
    # .claude/skills/memory/scripts/recall.py would be executed at every session.
    if [[ "${AGENTM_HOST:-claude-code}" == "claude-code" && -f ".claude/skills/memory/scripts/$script" ]]; then
        printf '%s\n' ".claude/skills/memory/scripts/$script"; return 0
    fi
    if [[ -f "$prefix/skills/memory/scripts/$script" ]]; then
        printf '%s\n' "$prefix/skills/memory/scripts/$script"; return 0
    fi
    local cfg="$prefix/.agentm-config.json"
    if [[ -f "$cfg" ]] && command -v python3 >/dev/null 2>&1; then
        local clone
        clone="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print((d.get("source_clones") or {}).get("agentm") or "")
' "$cfg" 2>/dev/null || true)"
        if [[ -n "$clone" && -f "$clone/harness/skills/memory/scripts/$script" ]]; then
            printf '%s\n' "$clone/harness/skills/memory/scripts/$script"; return 0
        fi
    fi
    return 1
}

RECALL_PY="$(_resolve_memory_script recall.py 2>/dev/null)" || RECALL_PY=""
if [[ -z "$RECALL_PY" ]]; then
    # Memory skill not installed under any known layout; graceful-skip.
    exit 0
fi

# Resolve MEMORY_ROOT: env → .agentm-config.json vault_path → none.
# recall.py requires this; Claude Code doesn't inject it into hook env, so
# pre-fix the hook found the script but recall.py silently exited 0 on
# "no vault configured".
_resolve_vault_path() {
    if [[ -n "${MEMORY_ROOT:-}" ]]; then
        printf '%s\n' "$MEMORY_ROOT"; return 0
    fi
    if [[ -n "${MEMORY_VAULT_PATH:-}" ]]; then
        printf '%s\n' "$MEMORY_VAULT_PATH"; return 0
    fi
    local cfg="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}/.agentm-config.json"
    if [[ -f "$cfg" ]] && command -v python3 >/dev/null 2>&1; then
        local v
        v="$(python3 -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
root = d.get("plugins.obsidian-vault.vault_path") or d.get("vault_path") or ""
rel = d.get("plugins.obsidian-vault.memory_root")
rel = rel.strip().replace(chr(92), "/") if isinstance(rel, str) else ""
if rel.startswith("/") or ":" in rel:
    rel = ""
parts = [s for s in rel.split("/") if s]
if root and parts and ".." not in parts:
    root = os.path.join(root, *parts)
print(root)
' "$cfg" 2>/dev/null || true)"
        if [[ -n "$v" ]]; then printf '%s\n' "$v"; return 0; fi
    fi
    return 1
}
_resolved_vault="$(_resolve_vault_path 2>/dev/null)" || _resolved_vault=""
if [[ -n "$_resolved_vault" ]]; then
    # The memory root, under its one name (operator ruling 2026-09-11).
    # MEMORY_VAULT_PATH is the deprecated alias with the same meaning, exported
    # alongside for one release because readers outside this repo (crickets'
    # installed plugins) still know only that name.
    export MEMORY_ROOT="$_resolved_vault"
    export MEMORY_VAULT_PATH="$_resolved_vault"
fi
unset _resolved_vault

# Require python3 — exit 0 if missing (graceful-skip; no blocking).
if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# Resolve the interpreter that runs the memory scripts.
#
# Pre-fix, every memory hook ended in a bare `python3 …`, which is a PATH
# lookup. On a stock macOS box that resolves to Apple's /usr/bin/python3, whose
# sqlite3 is built without `--enable-loadable-sqlite-extensions` and therefore
# has no `enable_load_extension` at all. sqlite-vec is a loadable native
# extension, so the vector index could never open, and every
# caller read that as the graceful "index not built yet" skip — semantic recall
# was structurally unreachable, silently, against a fully healthy 1035-row
# index. See harness/hooks/lib/resolve-python.sh for the resolution order and
# the reasoning behind each step; it is the canonical resolver, and
# scripts/machinery_doctor.py's `memory-hook-interpreter` check delegates to
# the same script so the diagnosis cannot drift from the behavior.
#
# The lib path is resolved relative to this hook file, which is identical in
# the repo (harness/hooks/<name>/ → harness/hooks/lib/) and in an install
# (<prefix>/hooks/<name>/ → <prefix>/hooks/lib/), so no config lookup is
# needed to find it. Falls back to bare `python3` — the pre-fix behavior — if
# the lib is missing, so a partial install degrades exactly as it used to.
#
# Note the ordering: this deliberately sits *after* the `command -v python3`
# gate and the config reads above, which stay on bare `python3`. Those only
# parse small JSON files, which any interpreter can do; only the memory
# scripts need the capable one, and bootstrapping the resolver off the config
# would be circular.
_resolve_agentm_python() {
    local lib
    lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/../lib/resolve-python.sh"
    if [[ -r "$lib" ]]; then
        local resolved
        resolved="$(bash "$lib" 2>/dev/null || true)"
        if [[ -n "$resolved" ]]; then printf '%s\n' "$resolved"; return 0; fi
    fi
    printf '%s\n' "python3"
}
AGENTM_PY="$(_resolve_agentm_python)"

# Invoke recall. recall.py handles MEMORY_ROOT resolution, glob,
# frontmatter parse, filter, output, and the 500ms time budget internally.
# We no longer `exec` it — the SessionStart pending-state briefing
# (orchestration_briefing.py) appends after the always-load recall
# (V4 #23 task 3, DC-3: agentm-native, non-blocking).
#
# The payload goes along for its `cwd`: a project's recall_excludes rule follows
# the session's folder, which on another host need not be this process's.
printf '%s' "$PAYLOAD" | "$AGENTM_PY" "$RECALL_PY" session-start --stdin-payload || true

# ── Pending-state briefing pass (V4 #23 task 3) ────────────────────────────
# Best-effort, non-blocking: scans the vault for over-threshold pending signals
# (inbox / HIGH skill-watchlist / incubator / stale idea-ledger) and appends a
# tight briefing block — but ONLY when something shifted since last shown AND
# the cooldown allows (anti-fatigue guard in auto_orchestration.py). The
# generator swallows any error → empty output, so this never blocks session
# boot. orchestration_briefing.py is a sibling of recall.py in the same memory
# scripts dir, so the same resolver finds it across install scopes.
BRIEFING_PY="$(_resolve_memory_script orchestration_briefing.py 2>/dev/null)" || BRIEFING_PY=""
if [[ -n "$BRIEFING_PY" && "$HOST_IS_CLAUDE" == 1 ]]; then
    if [[ -n "${MEMORY_ROOT:-}" ]]; then
        "$AGENTM_PY" "$BRIEFING_PY" --vault-path "$MEMORY_ROOT" 2>/dev/null || true
    else
        "$AGENTM_PY" "$BRIEFING_PY" 2>/dev/null || true
    fi
fi

exit 0
