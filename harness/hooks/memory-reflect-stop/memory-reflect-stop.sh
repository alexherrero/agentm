#!/usr/bin/env bash
# memory-reflect-stop — mine the just-ended session's transcript on Stop.
#
# Fires on Claude Code's Stop event. Parses the stdin JSON payload for
# transcript_path (authoritative; sent on every hook event) plus session_id and
# cwd, and invokes reflect.py to mine durable candidates. Hosts too old to send
# transcript_path fall back to the computed
# ~/.claude/projects/<cwd-slug>/<session_id>.jsonl. Output on stdout
# (pass-through from reflect.py); transparency line on stderr.
#
# Tri-modal routing (HIGH→auto / MEDIUM→interactive / LOW→inbox) lands in
# plan #7a part 3 task 5; this hook ships the mining-only scaffold.
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

REFLECT_PY="$(_resolve_memory_script reflect.py 2>/dev/null)" || REFLECT_PY=""
if [[ -z "$REFLECT_PY" ]]; then
    exit 0
fi

# Resolve MEMORY_VAULT_PATH from .agentm-config.json if not in env. See
# memory-recall-session-start.sh for rationale + bug history.
_resolve_vault_path() {
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
# and keep session_id + cwd for the marker and the fallback below.
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

# ── Phase-dispatch dedup guard (V4 #23 task 5) ─────────────────────────────
# If a .reflected marker for this session already exists, the post-/work
# phase-integration dispatch (orchestration_phase.py) already reflected this
# transcript and renamed .start → .reflected. Re-reflecting here would
# double-route (the HIGH save errors on a slug collision), so skip — the work
# is already done. They cooperate via this marker so the same session is never
# reflected twice, whichever fires first.
REFLECTED_MARKER=".harness/session-id-${SESSION_ID}.reflected"
if [[ -f "$REFLECTED_MARKER" ]]; then
    echo "[memory-reflect-stop] session ${SESSION_ID} already reflected (phase dispatch); skipping" >&2
    exit 0
fi

# Invoke reflect.py with --summary + --route. The routing pass auto-saves
# HIGH candidates to canonical paths + sends MEDIUM/LOW + ideas to _inbox/.
# Route-mode defaults to "auto" (hook-safe; never prompts; MEDIUM → _inbox/)
# unless operator sets MEMORY_REVIEW_MODE=silent (auto-save MEDIUM) or
# MEMORY_REVIEW_MODE=interactive (which falls back to auto here since hooks
# have no TTY).
#
# We capture output once + reuse for both the transparency line + stdout
# pass-through. Running reflect.py --route twice would error on slug
# collision (HIGH save would refuse the second time).
REFLECT_OUT="$("$AGENTM_PY" "$REFLECT_PY" "$TRANSCRIPT" --summary --route 2>&1)"
REFLECT_EXIT=$?
if [[ $REFLECT_EXIT -ne 0 ]]; then
    # Most common cause: MEMORY_VAULT_PATH not set in hook env. Reflection
    # output is captured but routing failed; emit what we have + stderr note.
    echo "$REFLECT_OUT" >&2 | head -3
    echo "[memory-reflect-stop] reflect.py --route exited $REFLECT_EXIT (MEMORY_VAULT_PATH set?); transcript was $TRANSCRIPT" >&2
    exit 0
fi

# Extract counts from the summary + route records for the transparency line.
SUMMARY_LINE="$(printf '%s' "$REFLECT_OUT" | grep -m1 '"pass": "summary"')"
ROUTE_LINE="$(printf '%s' "$REFLECT_OUT" | grep -m1 '"pass": "route"')"

MEM_COUNT="$(printf '%s' "$SUMMARY_LINE" | python3 -c 'import json,sys;
try:
    d=json.loads(sys.stdin.read()); print(d.get("memory_candidate_count",0))
except: print(0)' 2>/dev/null || echo 0)"
IDEA_COUNT="$(printf '%s' "$SUMMARY_LINE" | python3 -c 'import json,sys;
try:
    d=json.loads(sys.stdin.read()); print(d.get("idea_candidate_count",0))
except: print(0)' 2>/dev/null || echo 0)"

if [[ -n "$ROUTE_LINE" ]]; then
    SAVED="$(printf '%s' "$ROUTE_LINE" | python3 -c 'import json,sys;
try:
    d=json.loads(sys.stdin.read()); print(d.get("auto_saved",0)+d.get("approved",0))
except: print(0)' 2>/dev/null || echo 0)"
    INBOXED="$(printf '%s' "$ROUTE_LINE" | python3 -c 'import json,sys;
try:
    d=json.loads(sys.stdin.read()); print(d.get("inboxed",0)+d.get("ideas_inboxed",0))
except: print(0)' 2>/dev/null || echo 0)"
    echo "[memory-reflect-stop] Mined ${MEM_COUNT} memory + ${IDEA_COUNT} idea candidates from $TRANSCRIPT; saved $SAVED, inboxed $INBOXED" >&2
else
    echo "[memory-reflect-stop] Mined ${MEM_COUNT} memory + ${IDEA_COUNT} idea candidates from $TRANSCRIPT (routing skipped)" >&2
fi

# Emit captured reflect.py output on stdout (one JSON record per line).
printf '%s\n' "$REFLECT_OUT"

# ── Crash-recovery marker rename (plan #7a part 3 task 6) ──────────────────
# Reflection succeeded → rename .harness/session-id-<sid>.start → .reflected.
# This marks the session as fully reflected; the idle hook's orphan scan
# (memory-reflect-idle) will GC the .reflected marker after 30 days.
# If the .start marker doesn't exist (operator never ran SessionStart, or
# .harness/ wasn't initialized), this is a no-op.
MARKER=".harness/session-id-${SESSION_ID}.start"
if [[ -f "$MARKER" ]]; then
    # Stamped NOW after the rename: mv preserves the .start mtime, which records
    # when the SESSION STARTED. The idle hook compares this marker against the
    # transcript's mtime to decide whether a re-created .start has anything new
    # behind it, and that comparison needs the time of the REFLECTION.
    if mv "$MARKER" "${MARKER%.start}.reflected" 2>/dev/null; then
        touch "${MARKER%.start}.reflected" 2>/dev/null || true
    fi
fi

exit 0
