#!/usr/bin/env bash
# memory-reflect-stop — keep the session's episodic trace current on Stop.
#
# Fires on Claude Code's Stop event. Parses the stdin JSON payload for
# transcript_path (authoritative; sent on every hook event) plus session_id and
# cwd, and rewrites the session's trace with episodic_trace.py. Hosts too old
# to send transcript_path fall back to the computed
# ~/.claude/projects/<cwd-slug>/<session_id>.jsonl.
#
# It mines nothing (task 199). Lessons are compiled at night from every
# session's new slice, keyed by a per-session bookmark in the engine's state
# directory (lesson_compile.py); this hook used to mine a session once, at its
# first Stop, and every later turn was never read.
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block session end.

# Resolve reflect.py across install scopes (project → user → source-clone).
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

TRACE_PY="$(_resolve_memory_script episodic_trace.py 2>/dev/null)" || TRACE_PY=""
if [[ -z "$TRACE_PY" ]]; then
    exit 0
fi

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

# Stop hook stdin payload (per Claude Code hook spec): JSON carrying session_id,
# cwd, and transcript_path on every hook event. We read transcript_path directly
# and keep session_id + cwd for the trace and the fallback below.
# Read stdin into a variable so we can parse it WITHOUT requiring jq (not
# universally installed; Python json module is always present alongside python3).
PAYLOAD="$(cat 2>/dev/null || true)"
if [[ -z "$PAYLOAD" ]]; then
    echo "[memory-reflect-stop] no stdin payload (skipping)" >&2
    exit 0
fi

# Parse session_id + cwd + transcript_path via a one-liner Python invocation.
# Returns "<session_id>\t<cwd>\t<transcript_path>" or empty on parse failure.
PARSED="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get("session_id") or ""
cwd = d.get("cwd") or ""
tp = d.get("transcript_path") or ""
if sid:
    print(f"{sid}\t{cwd}\t{tp}")
' 2>/dev/null)"

if [[ -z "$PARSED" ]]; then
    echo "[memory-reflect-stop] no session_id on stdin (skipping)" >&2
    exit 0
fi

SESSION_ID="$(printf '%s' "$PARSED" | cut -f1)"
CWD="$(printf '%s' "$PARSED" | cut -f2)"
PAYLOAD_TRANSCRIPT="$(printf '%s' "$PARSED" | cut -f3)"
if [[ -z "$CWD" ]]; then
    CWD="$(pwd)"
fi

# Transcript path: the payload's own `transcript_path` is authoritative.
#
# Claude Code sends it on every hook event and its docs say to use it rather
# than constructing one from session_id, because that correspondence is not
# guaranteed — resume, compact, fork and `/branch` all mint session ids whose
# transcript may live elsewhere or not exist yet under the computed name.
#
# The computed path stays as a FALLBACK for hosts too old to send the field.
# Deleting it would turn "this host predates transcript_path" into "reflection
# silently never runs", which is precisely the failure this hook already spent
# 57 days in. When the payload does carry the field, the fallback never runs.
if [[ -n "$PAYLOAD_TRANSCRIPT" ]]; then
    TRANSCRIPT="$PAYLOAD_TRANSCRIPT"
else
    # Fallback: ~/.claude/projects/<cwd-slug>/<session_id>.jsonl where
    # <cwd-slug> = CWD with '/' and '.' both replaced by '-'.
    #
    # NO extra leading '-': the path is absolute, so `tr` already turns its
    # leading '/' into the leading '-'. Prefixing another one produced
    # '--Users-...', which matches no directory Claude Code ever creates — so
    # every lookup missed, this hook logged "transcript not found (skipping)"
    # on every session, and reflection silently never ran at all. Dots convert
    # too ('/x/.claude/y' -> '-x--claude-y'), which is what makes worktree
    # sessions resolve. Pinned to literals by scripts/test_transcript_slug.py.
    CWD_SLUG="$(printf '%s' "$CWD" | tr '/.' '--')"
    TRANSCRIPT="$HOME/.claude/projects/${CWD_SLUG}/${SESSION_ID}.jsonl"
fi

# A payload-supplied path that does not exist is reported and skipped, never
# quietly retried against the computed one — falling back there would be
# guessing again, and guessing is what this whole change retires.
if [[ ! -f "$TRANSCRIPT" ]]; then
    echo "[memory-reflect-stop] transcript not found: $TRANSCRIPT (skipping)" >&2
    exit 0
fi

# Filing v2 remainders task 7: the session's episodic trace — what it captured
# and recalled, as links under memory/episodic/ — rewritten on every Stop, so
# every turn reaches it, and with it every idea said after the first turn.
# Best-effort: a trace never blocks session end.
"$AGENTM_PY" "$TRACE_PY" "$TRANSCRIPT" --session "$SESSION_ID" --surface claude-code >/dev/null 2>&1 || true

exit 0
