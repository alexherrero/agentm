#!/usr/bin/env bash
# memory-automemory-note — record a Claude Code auto-memory note for tonight.
#
# A PostToolUse hook on Write, Edit and MultiEdit. When a session saves a note
# under ~/.claude/projects/<folder>/memory/, the note's path is appended to the
# lesson compile's input queue in the engine's state directory, and the night's
# compile reads it with the rest of that project's day (task 199). Nothing is
# filed from here, so a note cannot bypass the compile's daily cap.
#
# THE PRE-FILTER. This fires on every file write on the machine, so a payload
# that does not name an auto-memory folder exits before any Python starts.
#
# IT FAILS OPEN. No python3, no payload, or a script it cannot find: exit 0.

set -uo pipefail

payload="$(cat 2>/dev/null || true)"
[[ -n "$payload" ]] || exit 0
[[ "$payload" == */.claude/projects/*/memory/* ]] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0

_resolve_memory_script() {  # $1 = script basename
    local script="$1"
    local prefix="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
    if [[ -f "$prefix/skills/memory/scripts/$script" ]]; then
        printf '%s\n' "$prefix/skills/memory/scripts/$script"; return 0
    fi
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || return 1
    if [[ -f "$here/../../skills/memory/scripts/$script" ]]; then
        printf '%s\n' "$here/../../skills/memory/scripts/$script"; return 0
    fi
    return 1
}
COMPILE_PY="$(_resolve_memory_script lesson_compile.py 2>/dev/null)" || exit 0

printf '%s' "$payload" | PYTHONDONTWRITEBYTECODE=1 python3 "$COMPILE_PY" note-automemory >/dev/null 2>&1 || true
exit 0
