#!/usr/bin/env bash
# memory-reflect-idle — retire the old session markers and fire the idle chain.
#
# Fires on Claude Code's SessionStart event. Until task 199 it swept
# .harness/session-id-*.start markers for sessions whose Stop never fired and
# mined them retroactively. Mining moved to the night (lesson_compile.py),
# keyed by a per-session bookmark in the engine's state directory, so the
# markers retired: this hook deletes any it finds, .start and .reflected
# alike, and then launches the cooldown-gated idle chain.
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block session start.

# Resolve memory-skill scripts across install scopes (project → user → source-clone).
# See memory-recall-session-start.sh for the rationale + bug history.
_resolve_memory_script() {  # $1 = script basename
    local script="$1"
    local prefix="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
    if [[ -f ".claude/skills/memory/scripts/$script" ]]; then
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

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# Resolve the interpreter that runs the memory scripts. See
# memory-recall-session-start.sh for the rationale + bug history.
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

# Resolve MEMORY_ROOT from .agentm-config.json if not in env. See
# memory-recall-session-start.sh for rationale + bug history.
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

# ── Retired markers (task 199) ─────────────────────────────────────────────
# Nothing writes a session marker any more and nothing reads one, so whatever
# an earlier version left behind is deleted on sight. A marker was a pointer to
# a transcript, never the transcript, so deleting one loses nothing.
shopt -s nullglob
old_markers=(.harness/session-id-*.start .harness/session-id-*.reflected)
shopt -u nullglob
if (( ${#old_markers[@]} > 0 )); then
    rm -f "${old_markers[@]}" 2>/dev/null || true
    echo "[memory-reflect-idle] cleared ${#old_markers[@]} retired session markers" >&2
fi

# ── Idle orchestration chain (V4 #23 task 4) ──────────────────────────────
# Fire the cooldown-gated chain driver: discover-skills (cadence-checked) →
# adapt_skills Pass-1 (≤3 candidates staged for the adapt-evaluator). The driver self-gates on the idle_chain
# cooldown (default 24h) + the enable_idle_chain toggle, so most invocations
# are a fast no-op; when it DOES fire it can take longer than this hook's 30s
# SessionStart timeout (corpus mining + GitHub enrichment), so we run it
# DETACHED — a subshell background job, reparented away from the hook — and
# return immediately. Killing the hook at 30s would otherwise leave the chain
# never recording its fire, re-running every session. The chain's results
# surface on the NEXT session via the task-3 briefing. Requires
# MEMORY_ROOT; graceful-skip if unset / driver absent.
ORCH_IDLE_PY="$(_resolve_memory_script orchestration_idle.py 2>/dev/null)" || ORCH_IDLE_PY=""
if [[ -n "$ORCH_IDLE_PY" && -n "${MEMORY_ROOT:-}" ]]; then
    ( "$AGENTM_PY" "$ORCH_IDLE_PY" --vault-path "$MEMORY_ROOT" >/dev/null 2>&1 & ) 2>/dev/null || true
fi

exit 0
