#!/usr/bin/env bash
# session-cost-capture — the Stop-hook capture half absorbed from the
# never-staged PLAN-session-cost-capture micro-plan (2026-07-05 decision
# record, PLAN-wave-d-tokens-and-privacy task 1).
#
# Fires on Claude Code's Stop event. Parses the stdin JSON payload for
# session_id + cwd, computes the transcript path at
# ~/.claude/projects/<cwd-slug>/<session_id>.jsonl (same formula agentm's
# memory-reflect-stop hook uses), and invokes session_cost_writer.py to
# append one `session-cost` telemetry event per model observed in that
# session to the device-local event log (PLAN-observability-ledger task 1 —
# retargeted off the vault). Capture-half only — no trend analysis here
# (see dreaming_trend_stub.py, staged dark pending Wave-E).
#
# Graceful no-op contract (must never block session close): missing
# script/transcript/python3 all exit 0 silently (or with a stderr note).
#
# Canonical source: crickets' src/tokens/hooks/session-cost-capture/
# session-cost-capture.sh. This is a manual standalone install into
# agentm's .claude/hooks/ (Consolidation arc, CONS-9 step 0) — the
# canonical script assumes either a plugin install ($CLAUDE_PLUGIN_ROOT
# set) or living inside crickets' own src/ tree; neither holds for a
# standalone copy in a sibling repo, so this copy adds a third fallback:
# the conventional sibling-checkout path (matching the $CRICKETS_SCRIPTS_DIR
# override convention this arc's other cross-repo bridges already use,
# e.g. src_model.py's find_agentm_scripts_dir()).
#
# See hook.md in the canonical source directory for full documentation.

set -uo pipefail  # no -e — must never block session end.

PAYLOAD="$(cat 2>/dev/null || true)"
if [[ -z "$PAYLOAD" ]]; then
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# session_cost_writer.py ships alongside this hook's plugin payload.
WRITER_PY="${CLAUDE_PLUGIN_ROOT:-}/scripts/session_cost_writer.py"
if [[ -z "${CLAUDE_PLUGIN_ROOT:-}" || ! -f "$WRITER_PY" ]]; then
    # Dev-checkout convention: this repo's own src/ tree (holds only when
    # this script lives inside a crickets checkout).
    _here_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    for candidate in \
        "$_here_dir/../../scripts/session_cost_writer.py" \
        "$_here_dir/../../../tokens/scripts/session_cost_writer.py"; do
        if [[ -f "$candidate" ]]; then
            WRITER_PY="$candidate"
            break
        fi
    done
fi
if [[ ! -f "$WRITER_PY" ]]; then
    # Standalone-copy convention: a sibling crickets checkout, same
    # override/default pattern this arc's other cross-repo bridges use.
    for candidate in \
        "${CRICKETS_SCRIPTS_DIR:-}/session_cost_writer.py" \
        "$HOME/Antigravity/crickets/src/tokens/scripts/session_cost_writer.py"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            WRITER_PY="$candidate"
            break
        fi
    done
fi
if [[ ! -f "$WRITER_PY" ]]; then
    exit 0
fi

# Parse session_id + cwd from the Stop payload (per Claude Code's hook spec).
PARSED="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get("session_id") or ""
cwd = d.get("cwd") or ""
if sid:
    print(f"{sid}\t{cwd}")
' 2>/dev/null)"

if [[ -z "$PARSED" ]]; then
    exit 0
fi

SESSION_ID="$(printf '%s' "$PARSED" | cut -f1)"
CWD="$(printf '%s' "$PARSED" | cut -f2)"
if [[ -z "$CWD" ]]; then
    CWD="$(pwd)"
fi

# Compute transcript path: ~/.claude/projects/<cwd-slug>/<session_id>.jsonl
# NOTE: fixed a real bug found during CONS-9 verification (Consolidation
# arc) — the canonical script prepended an extra literal "-" on top of what
# `tr '/' '-'` already produces from the leading "/" in an absolute path,
# doubling the leading dash and never matching a real ~/.claude/projects/
# directory name. Confirmed against a real transcript directory before
# fixing; flagged upstream in crickets since the same bug is likely in
# every install of this hook, not just this one.
CWD_SLUG="$(printf '%s' "$CWD" | tr '/' '-')"
TRANSCRIPT="$HOME/.claude/projects/${CWD_SLUG}/${SESSION_ID}.jsonl"

if [[ ! -f "$TRANSCRIPT" ]]; then
    exit 0
fi

python3 "$WRITER_PY" "$TRANSCRIPT" --session-id "$SESSION_ID" 2>&1 | sed 's/^/[session-cost-capture] /' >&2 || true

exit 0
