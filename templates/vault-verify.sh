#!/usr/bin/env bash
# vault-verify (AgentM) — the vault's verify script: check a note when it is saved.
#
# Installed to <vault>/.harness/verify.sh by install.sh and install.ps1, and
# rewritten by every later install while this first line still says AgentM put
# it there. Delete that line to keep your own edits.
#
# The machine-wide verify-dispatch PostToolUse hook walks up from every file a
# Claude Code session writes or edits to the nearest `.harness/verify.sh`, and
# for a note in the vault that is this script. It hands the note to
# `scripts/vault_note_check.py` in the AgentM clone, which asks it the three
# vault gates' questions: does the header parse, is its type registered, and
# does a tracker hold.
#
# Silence is the default: a clean note, a file that isn't Markdown and a
# machine missing any piece below all exit 0 with nothing printed. A note with
# a problem exits 2 with the problem on stderr, which Claude Code hands back to
# the session that saved it. The save itself stands; the session fixes the
# note. Scripts, the daemon and other hosts' sessions don't pass through here;
# the nightly sweep names what they leave in the morning note.

set -uo pipefail

file="${1:-}"
[[ "$file" == *.md && -f "$file" ]] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# The clone, from the install config, the way the memory hooks find it.
prefix="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}"
clone="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print((d.get("source_clones") or {}).get("agentm") or "")
' "$prefix/.agentm-config.json" 2>/dev/null || true)"
check="$clone/scripts/vault_note_check.py"
[[ -n "$clone" && -f "$check" ]] || exit 0

# The interpreter the memory hooks run on, and the daemon that reads the
# filing contract: a hook's PATH is often thinner than a terminal's.
py="python3"
if [[ -r "$prefix/hooks/lib/resolve-python.sh" ]]; then
    py="$(bash "$prefix/hooks/lib/resolve-python.sh" 2>/dev/null || true)"
    [[ -n "$py" ]] || py="python3"
fi
if [[ -z "${AGENTMD:-}" && -x "$HOME/.local/bin/agentmd" ]]; then
    export AGENTMD="$HOME/.local/bin/agentmd"
fi

PYTHONDONTWRITEBYTECODE=1 exec "$py" "$check" --file "$file"
