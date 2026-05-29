#!/usr/bin/env bash
# harness-context-session-start — inject the project's vault PLAN.md/progress.md
# paths into session context on SessionStart.
#
# Post-V4 #26 a project's harness state lives at <vault>/projects/<slug>/_harness/,
# not in the repo's .harness/. This hook tells the agent where, on every session
# boot, so it reads PLAN.md before plan-status questions or phase commands. Only
# fires the injection when BOTH PLAN.md and progress.md resolve + exist on disk;
# silent no-op otherwise. See hook.md for full docs. V4 #39.

set -uo pipefail   # NOTE: no -e — must never block session boot (graceful-skip).

# ── Read the SessionStart event JSON; extract cwd (DC-6: event cwd, not $PWD) ──
PAYLOAD="$(cat 2>/dev/null || true)"
command -v python3 >/dev/null 2>&1 || { echo "[harness-context] python3 unavailable — skipped" >&2; exit 0; }

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
if [[ ! -d "$EVENT_CWD" ]]; then
    echo "[harness-context] event cwd not a directory — skipped" >&2
    exit 0
fi

# ── Resolve harness_memory.py: recorded agentm source clone → fallback ─────────
RESOLVER=""
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
    if [[ -n "$AGENTM_CLONE" && -f "$AGENTM_CLONE/scripts/harness_memory.py" ]]; then
        RESOLVER="$AGENTM_CLONE/scripts/harness_memory.py"
    fi
fi
if [[ -z "$RESOLVER" && -f "$HOME/Antigravity/agentm/scripts/harness_memory.py" ]]; then
    RESOLVER="$HOME/Antigravity/agentm/scripts/harness_memory.py"
fi
if [[ -z "$RESOLVER" ]]; then
    echo "[harness-context] harness_memory.py resolver unavailable — skipped" >&2
    exit 0
fi

# ── 500ms budget wrapper (gtimeout/timeout if present; bare otherwise) ─────────
TIMEOUT_CMD=""
if command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout 0.5"
elif command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout 0.5"
fi

_resolve_state() {  # $1 = state filename (PLAN.md | progress.md)
    ( cd "$EVENT_CWD" 2>/dev/null && $TIMEOUT_CMD python3 "$RESOLVER" vault-state-path "$1" 2>/dev/null )
}

PLAN_PATH="$(_resolve_state PLAN.md || true)"
PROGRESS_PATH="$(_resolve_state progress.md || true)"

# ── Inject only when BOTH resolve AND exist on disk ────────────────────────────
if [[ -n "$PLAN_PATH" && -n "$PROGRESS_PATH" && -f "$PLAN_PATH" && -f "$PROGRESS_PATH" ]]; then
    cat <<EOF
[agentm] Project state for this repo lives in the vault, not in .harness/:
  PLAN.md:     $PLAN_PATH
  progress.md: $PROGRESS_PATH
Read PLAN.md before answering plan-status questions or running /work, /review, /release.
EOF
    # Transparency line on stderr. Slug = parent-of-_harness dir name.
    SLUG="$(basename "$(dirname "$(dirname "$PLAN_PATH")")" 2>/dev/null || echo '?')"
    echo "[harness-context] injected vault paths for slug=$SLUG" >&2
else
    # No vault PLAN/progress resolved. This may be an UNCONFIGURED project that
    # should be offered auto-detect (V4 #32). Delegate the decision to
    # project_config.py should-nudge — it gates on: cwd has .git AND not already
    # registered AND no .agentm-no-register marker AND not a harness-source
    # bypass. All nudge logic lives in testable Python; the hook only emits.
    PC="$(dirname "$RESOLVER")/project_config.py"
    if [[ -f "$PC" ]] && ( cd "$EVENT_CWD" 2>/dev/null && $TIMEOUT_CMD python3 "$PC" should-nudge . >/dev/null 2>&1 ); then
        echo "[agentm] New project — I haven't configured this repo. Say 'configure this project' or run /setup --detect."
        echo "[harness-context] configure-nudge emitted for $EVENT_CWD" >&2
    else
        echo "[harness-context] non-harness cwd or vault paths unresolved — skipped" >&2
    fi
fi

exit 0
