#!/usr/bin/env bash
# harness-context-session-start — inject the project's PLAN.md/progress.md
# paths into session context on SessionStart.
#
# V5-3: harness state lives in <project_root>/.harness/ (device-local only).
# This hook tells the agent where, on every session boot, so it reads PLAN.md
# before plan-status questions or phase commands. Only fires the injection when
# PLAN.md or PLAN-*.md exists in .harness/; silent no-op otherwise.
# See hook.md for full docs. V4 #39, V5-3 update.

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

# ── V5-3: state is always device-local in <project_root>/.harness/ ─────────────
HARNESS_DIR="$EVENT_CWD/.harness"
PLAN_PATH="$HARNESS_DIR/PLAN.md"
PROGRESS_PATH="$HARNESS_DIR/progress.md"

# ── Named plans (V5-10): enumerate PLAN-<name>.md in .harness/ ─────────────────
# Glob PLAN-*.md, skipping GDrive conflict copies. Back-compat: a harness holding
# only the unnamed PLAN.md finds zero named plans and falls through to the LOCKED
# singleton block below, byte-identical.
NAMED_PLANS=()
if [[ -d "$HARNESS_DIR" ]]; then
    shopt -s nullglob
    for _pf in "$HARNESS_DIR"/PLAN-*.md; do
        case "$(basename "$_pf")" in
            *"(conflicted copy"*) continue ;;   # GDrive conflict copy - not a plan
        esac
        NAMED_PLANS+=("$_pf")
    done
    shopt -u nullglob
fi

# ── Inject: named-plan mode → singleton (DC-7, locked) → nudge/skip ────────────
if [[ ${#NAMED_PLANS[@]} -gt 0 ]]; then
    # Named-plan mode: surface every PLAN*.md + the .harness/active-plan binding.
    {
        echo "[agentm] Project state for this repo lives in .harness/:"
        echo "Named-plan mode - this repo has more than one active plan:"
        # Unnamed singleton first (only if it exists), then named alphabetically
        # (the glob expands sorted). Each plan's progress is its progress-<name>.md.
        [[ -n "$PLAN_PATH" && -f "$PLAN_PATH" ]] && printf '  %-22s %s\n' "PLAN.md" "$PLAN_PATH"
        for _pf in "${NAMED_PLANS[@]}"; do
            printf '  %-22s %s\n' "$(basename "$_pf")" "$_pf"
        done
        # Active-plan binding — the worktree-local .harness/active-plan marker
        # (read here, written by component (2)'s worktree-spawn helper). A present
        # marker naming an absent PLAN-<name>.md is dangling — surfaced, not fatal.
        MARKER="$EVENT_CWD/.harness/active-plan"
        if [[ -f "$MARKER" ]]; then
            BIND="$(head -n1 "$MARKER" 2>/dev/null | tr -d '[:space:]')"
            if [[ -n "$BIND" ]]; then
                if [[ -f "$HARNESS_DIR/PLAN-$BIND.md" ]]; then
                    echo "Active plan (.harness/active-plan -> $BIND): $HARNESS_DIR/PLAN-$BIND.md"
                else
                    echo "Active plan (.harness/active-plan -> $BIND): DANGLING - PLAN-$BIND.md not found; run doctor."
                fi
            fi
        fi
        echo "Read the plan you own (or the .harness/active-plan one) before /work, /review, /release."
    }
    SLUG="$(basename "$EVENT_CWD" 2>/dev/null || echo '?')"
    echo "[harness-context] injected ${#NAMED_PLANS[@]} named plan(s) for slug=$SLUG" >&2

elif [[ -n "$PLAN_PATH" && -n "$PROGRESS_PATH" && -f "$PLAN_PATH" && -f "$PROGRESS_PATH" ]]; then
    # Singleton path — LOCKED DC-7 4-line block, byte-identical (back-compat).
    cat <<EOF
[agentm] Project state for this repo lives in .harness/:
  PLAN.md:     $PLAN_PATH
  progress.md: $PROGRESS_PATH
Read PLAN.md before answering plan-status questions or running /work, /review, /release.
EOF
    # Transparency line on stderr. Slug = basename of project root dir.
    SLUG="$(basename "$EVENT_CWD" 2>/dev/null || echo '?')"
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
