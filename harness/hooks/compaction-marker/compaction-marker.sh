#!/usr/bin/env bash
# compaction-marker.sh — record in durable state that a compaction happened here.
#
# A PreCompact hook, registered once for the machine. Compaction discards the
# conversation; this leaves a dated marker in the project's progress log so the
# entries above it are readable as "written before the context was lost" rather
# than as one continuous history.
#
# It earns its keep mainly on AUTO-compaction. A deliberate `/compact` is a
# choice the operator remembers making; an automatic one happens mid-task with
# nothing said, and the next session reads a progress log whose recent entries
# came from a session that can no longer explain them.
#
# WHAT CHANGED FROM THE RETIRED PER-PROJECT VERSION. That one required
# `.harness/progress.md` relative to the cwd and appended to it directly. Both
# halves are now wrong: state may live in the vault rather than the repo, the
# active plan may be a named `progress-<slug>.md` rather than the singleton, and
# a direct append bypasses the vault write lock that the daemon and other
# sessions rely on. This hands the marker to `append-progress`, which resolves
# the active plan's log the way `resolve-active-plan` does — a task's
# `tasks/<name>/progress.md` in the vault, or a repo-local pair's — and appends
# through the storage backend, so the vault's write lock is held.

set -uo pipefail   # no -e: must never block a compaction (graceful-skip).

PAYLOAD="$(cat 2>/dev/null || true)"
command -v python3 >/dev/null 2>&1 || exit 0

# DC-6: the event's cwd, not $PWD. A hook's own working directory is not
# reliably the session's.
EVENT_CWD=""
TRIGGER="unknown"
CUSTOM=""
if [[ -n "$PAYLOAD" ]]; then
    read -r EVENT_CWD TRIGGER CUSTOM <<<"$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
cwd = (d.get("cwd") or "").replace("\t", " ").replace("\n", " ")
trig = (d.get("trigger") or "unknown").replace("\t", " ").replace("\n", " ")
cust = (d.get("custom_instructions") or "").replace("\t", " ").replace("\n", " ")
print(f"{cwd}\t{trig}\t{cust}")
' 2>/dev/null || true)"
fi
[[ -z "$EVENT_CWD" ]] && EVENT_CWD="$(pwd)"
[[ -d "$EVENT_CWD" ]] || exit 0
[[ -n "$TRIGGER" ]] || TRIGGER="unknown"

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
    TIMEOUT_CMD="gtimeout 5"
elif command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout 5"
fi

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
# `git rev-parse --abbrev-ref HEAD` in a repo with no commits prints "HEAD" to
# stdout AND exits non-zero, so the obvious `$(cmd || echo unknown)` captures
# BOTH and writes two lines into the marker. Capture first, judge after. "HEAD"
# itself means unborn or detached, which is not a branch name worth recording.
BRANCH="unknown"
_branch_raw="$(git -C "$EVENT_CWD" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -n "$_branch_raw" && "$_branch_raw" != "HEAD" ]]; then
    BRANCH="$_branch_raw"
fi

TMP="$(mktemp -t agentm-compaction.XXXXXX 2>/dev/null || echo "")"
[[ -n "$TMP" ]] || exit 0
trap 'rm -f "$TMP"' EXIT

{
    printf '\n'
    printf '## compaction event — %s\n' "$TS"
    printf -- '- trigger: %s\n' "$TRIGGER"
    printf -- '- branch: %s\n' "$BRANCH"
    [[ -n "$CUSTOM" ]] && printf -- '- /compact instructions: %s\n' "$CUSTOM"
    printf -- '- The session was compacted at this point. Entries above this marker\n'
    printf -- '  were written before the context was lost; the compaction summary\n'
    printf -- '  alone does not carry the per-file specifics /work and /review need.\n'
} > "$TMP"

# ── Append it to the active plan's log ───────────────────────────────────────
# append-progress picks the log from the worktree's active-plan marker, else a
# bare call: a task's log in the vault, or the repo-local pair's. A bare call on
# a project that keeps its plans in tasks exits 4, and a log that is absent or
# empty — not an initialized plan — is left alone; either way we leave silently.
# It writes through the storage backend, so this cannot race the daemon or
# another session writing the same log.
$TIMEOUT_CMD python3 "$RESOLVER" append-progress --project-root "$EVENT_CWD" \
    --content-file "$TMP" >/dev/null 2>&1 || true

exit 0
