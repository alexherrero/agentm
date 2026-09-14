#!/usr/bin/env bash
# project-brief-session-start — the opening brief for the project and task a
# session is bound to (agentm-vault § Projects and tasks: a session opens on
# twenty lines).
#
# Prints scripts/project_brief.py's brief when a tracker exists for the bound task
# or project. With none, it prints today's plan block by running
# harness-context-session-start with AGENTM_PLAN_BLOCK_ONLY=1, so the fallback is
# that hook's own rendering. Once this hook is registered, the context hook leaves
# its plan block here, so the block never prints twice. Kept apart from the
# always-load hook, whose output the host collapses unread. See hook.md.

set -uo pipefail   # NOTE: no -e — must never block session boot (graceful-skip).

PAYLOAD="$(cat 2>/dev/null || true)"
command -v python3 >/dev/null 2>&1 || { echo "[project-brief] python3 unavailable — skipped" >&2; exit 0; }

# ── The session's directory, from the event (not $PWD) ─────────────────────────
EVENT_CWD=""
if [[ -n "$PAYLOAD" ]]; then
    EVENT_CWD="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print(d.get("cwd") or "")
' 2>/dev/null || true)"
fi
[[ -z "$EVENT_CWD" ]] && EVENT_CWD="$(pwd)"

# ── Resolve project_brief.py: recorded agentm source clone → fallback ──────────
BRIEF=""
CFG="$HOME/.claude/.agentm-config.json"
if [[ -f "$CFG" ]]; then
    AGENTM_CLONE="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print((d.get("source_clones") or {}).get("agentm") or "")
' "$CFG" 2>/dev/null || true)"
    if [[ -n "$AGENTM_CLONE" && -f "$AGENTM_CLONE/scripts/project_brief.py" ]]; then
        BRIEF="$AGENTM_CLONE/scripts/project_brief.py"
    fi
fi
if [[ -z "$BRIEF" && -f "$HOME/Antigravity/agentm/scripts/project_brief.py" ]]; then
    BRIEF="$HOME/Antigravity/agentm/scripts/project_brief.py"
fi

# ── The brief, when a tracker exists ─────────────────────────────────────────────
if [[ -n "$BRIEF" && -d "$EVENT_CWD" ]]; then
    TIMEOUT_CMD=""
    if command -v gtimeout >/dev/null 2>&1; then
        TIMEOUT_CMD="gtimeout 2"
    elif command -v timeout >/dev/null 2>&1; then
        TIMEOUT_CMD="timeout 2"
    fi
    BRIEF_OUT="$($TIMEOUT_CMD python3 "$BRIEF" --cwd "$EVENT_CWD" 2>/dev/null)"
    BRIEF_RC=$?
    if [[ $BRIEF_RC -eq 0 && -n "$BRIEF_OUT" ]]; then
        printf '%s\n' "$BRIEF_OUT"
        echo "[project-brief] brief for $EVENT_CWD" >&2
        exit 0
    fi
fi

# ── No brief: today's plan block, rendered by the context hook itself ────────────
CONTEXT_HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/harness-context-session-start/harness-context-session-start.sh"
if [[ -f "$CONTEXT_HOOK" ]]; then
    printf '%s' "$PAYLOAD" | AGENTM_PLAN_BLOCK_ONLY=1 bash "$CONTEXT_HOOK"
else
    echo "[project-brief] no brief, and no context hook beside this one — skipped" >&2
fi
exit 0
