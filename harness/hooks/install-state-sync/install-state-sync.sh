#!/usr/bin/env bash
# install-state-sync.sh — SessionStart hook that re-merges divergent settings
# fragments per the recorded install state. Per V4 #30 plan #22 task 6.
#
# Non-blocking: exit 0 always. Graceful-skip if Python or the helper script
# is unavailable — the operator's session start path never breaks because
# of this hook.

set -uo pipefail

# Find the Python helper. It lives at scripts/install_state_sync.py in the
# source tree; under user-scope install it lands at the user-scope copy of
# scripts/. Search a few canonical locations.

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
    # No Python — graceful-skip
    exit 0
fi

# Helper resolution: prefer source clones if present; else look under
# install-prefix's scripts/.
INSTALL_PREFIX="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"

CANDIDATES=(
    "$HOME/Antigravity/agentm/scripts/install_state_sync.py"
    "$INSTALL_PREFIX/scripts/install_state_sync.py"
    "$INSTALL_PREFIX/../share/agentm/scripts/install_state_sync.py"
)

HELPER=""
for candidate in "${CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]]; then
        HELPER="$candidate"
        break
    fi
done

if [[ -z "$HELPER" ]]; then
    # Helper missing — graceful-skip (pre-V4 #30 install, or install layout
    # the hook doesn't recognize)
    exit 0
fi

# Run in quiet mode (no JSON to stdout); the helper emits stderr notice
# on re-merge or error.
"$PYTHON_BIN" "$HELPER" --install-prefix "$INSTALL_PREFIX" --quiet 2>&1
exit 0
