#!/usr/bin/env bash
# memory-recall-prompt-submit — inject query-relevant MemoryVault entries on prompt submit.
#
# Fires on Claude Code's UserPromptSubmit event. Receives the user's prompt
# (and other session metadata) as JSON on stdin; passes it through to
# recall.py's prompt-submit subcommand, which calls the recall engine for
# top-K relevant entries and emits them on stdout for context injection.
# Transparency line on stderr. Hard 300ms time budget; degraded-graceful
# on overrun.
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block the user prompt.

# Resolve recall.py across install scopes (project → user → source-clone).
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

RECALL_PY="$(_resolve_memory_script recall.py 2>/dev/null)" || RECALL_PY=""
if [[ -z "$RECALL_PY" ]]; then
    exit 0
fi

# Resolve MEMORY_VAULT_PATH from .agentm-config.json if not in env (Claude Code
# doesn't inject it into hook envs). See memory-recall-session-start.sh for
# rationale + bug history.
_resolve_vault_path() {
    if [[ -n "${MEMORY_VAULT_PATH:-}" ]]; then
        printf '%s\n' "$MEMORY_VAULT_PATH"; return 0
    fi
    local cfg="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}/.agentm-config.json"
    if [[ -f "$cfg" ]] && command -v python3 >/dev/null 2>&1; then
        local v
        v="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print(d.get("plugins.obsidian-vault.vault_path") or d.get("vault_path") or "")
' "$cfg" 2>/dev/null || true)"
        if [[ -n "$v" ]]; then printf '%s\n' "$v"; return 0; fi
    fi
    return 1
}
_resolved_vault="$(_resolve_vault_path 2>/dev/null)" || _resolved_vault=""
if [[ -n "$_resolved_vault" ]]; then
    export MEMORY_VAULT_PATH="$_resolved_vault"
fi
unset _resolved_vault

# Require python3 — exit 0 if missing.
if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# Pipe stdin (the UserPromptSubmit JSON payload) through to recall.py.
# recall.py handles MEMORY_VAULT_PATH resolution, JSON parsing, prompt
# extraction, recall engine query (lands in task 3), dedup, output, and
# the 300ms time budget internally.
exec python3 "$RECALL_PY" prompt-submit
