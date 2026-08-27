#!/usr/bin/env bash
# compaction-reanchor.sh — say that a compaction just happened, and that the
# summary is not enough.
#
# A SessionStart hook with matcher `compact`, so it fires only on the session
# that resumes from a compaction — never on an ordinary session start. Claude
# Code injects its stdout into the post-compaction context.
#
# WHY IT IS THIS SHORT. `harness-context-session-start` is registered with
# matcher `.*`, so it fires on this same event and already prints where the
# active plan and progress log live. Repeating that here would print it twice
# on exactly the sessions that are already short on context. What that hook
# does not and should not say — because it fires on every start — is that this
# particular session lost its conversation. That is the whole job here.
#
# The retired per-project version tested for a cwd-relative `.harness/PLAN.md`
# and hardcoded the three singleton filenames. Both are wrong now: state may
# live in the vault, and the active plan may be a named PLAN-<slug>.md. This
# one asks the seam whether the directory is a harness project at all and says
# nothing otherwise.

set -uo pipefail   # no -e: must never block session boot (graceful-skip).

PAYLOAD="$(cat 2>/dev/null || true)"
command -v python3 >/dev/null 2>&1 || exit 0

# DC-6: the event's cwd, not $PWD.
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
[[ -d "$EVENT_CWD" ]] || exit 0

# ── Resolve harness_memory.py: recorded agentm source clone → fallback ────────
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
[[ -n "$RESOLVER" ]] || exit 0

TIMEOUT_CMD=""
if command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout 2"
elif command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout 2"
fi

# resolve-active-plan answers with PATHS, not with existence: it will happily
# name <dir>/.harness/PLAN.md for a directory that has no harness at all. So a
# non-empty pair proves nothing, and both files must be checked on disk before
# this hook says anything. (harness-context-session-start states the same rule
# for the same reason — it speaks "only when both PLAN.md and progress.md
# resolve AND exist on disk".) Without this the hook lectures every compacted
# session on the machine about a plan that does not exist.
PAIR="$($TIMEOUT_CMD python3 "$RESOLVER" resolve-active-plan --project-root "$EVENT_CWD" 2>/dev/null || true)"
[[ -n "$PAIR" ]] || exit 0

PLAN_PATH="$(printf '%s' "$PAIR" | awk -F'\t' '{print $1}')"
PROGRESS_PATH="$(printf '%s' "$PAIR" | awk -F'\t' '{print $2}')"
[[ -n "$PLAN_PATH" && -f "$PLAN_PATH" ]] || exit 0
[[ -n "$PROGRESS_PATH" && -f "$PROGRESS_PATH" ]] || exit 0
PROGRESS_NAME="$(basename "$PROGRESS_PATH")"

cat <<EOF
[agentm] This session resumed from a **compaction** — the previous conversation
was discarded, not paused.

Read the durable state before continuing. The compaction summary preserves
themes and loses specifics: which files were mid-edit, which assertion was
failing, which decision was already settled and should not be reopened.

Look for the most recent \`## compaction event\` marker in ${PROGRESS_NAME} —
everything above it was written by the session whose context is now gone.

(The session-start context block printed alongside this one names where the
plan and progress log actually live.)
EOF

exit 0
