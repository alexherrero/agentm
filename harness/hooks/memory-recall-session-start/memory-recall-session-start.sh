#!/usr/bin/env bash
# memory-recall-session-start — load MemoryVault always-load entries on session boot.
#
# Fires on Claude Code's SessionStart event. Calls the memory skill's recall.py
# helper to glob _always-load/*.md entries + emit their bodies on stdout (which
# Claude Code injects as additional session context). Transparency line on
# stderr. Hard 500ms time budget; degraded-graceful on overrun.
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block session boot.

# ── Crash-recovery marker (plan #7a part 3 task 6) ─────────────────────────
# Parse the SessionStart event's stdin JSON for transcript_path, session_id,
# cwd and source, then write a `.harness/session-id-<sid>.start` marker. The
# marker enables the idle hook's orphan-recovery sweep — if Stop never fires
# (Claude Code crashed, OS killed it, force quit), the marker stays as .start
# past the idle threshold and the idle hook reflects retroactively on next
# SessionStart.
#
# Marker writes are best-effort: failure here doesn't block recall (which is
# the primary purpose of this hook). If .harness/ doesn't exist or session_id
# can't be parsed, we skip the marker + continue to recall.
PAYLOAD="$(cat 2>/dev/null || true)"
if [[ -n "$PAYLOAD" ]] && [[ -d .harness || -w . ]]; then
    PARSED="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get("session_id") or ""
cwd = d.get("cwd") or ""
tp = d.get("transcript_path") or ""
src = d.get("source") or ""
if sid:
    print(f"{sid}\t{cwd}\t{tp}\t{src}")
' 2>/dev/null)"
    if [[ -n "$PARSED" ]]; then
        SESSION_ID="$(printf '%s' "$PARSED" | cut -f1)"
        SESSION_CWD="$(printf '%s' "$PARSED" | cut -f2)"
        PAYLOAD_TRANSCRIPT="$(printf '%s' "$PARSED" | cut -f3)"
        SESSION_SOURCE="$(printf '%s' "$PARSED" | cut -f4)"
        if [[ -z "$SESSION_CWD" ]]; then
            SESSION_CWD="$(pwd)"
        fi
        # Transcript path: the payload's own `transcript_path` is authoritative
        # (see memory-reflect-stop.sh for the full rationale — Claude Code sends
        # it on every event, and the session-id → filename correspondence it
        # replaces is not guaranteed across resume / compact / fork / `/branch`).
        # The computed path stays as a fallback for hosts too old to send it.
        if [[ -n "$PAYLOAD_TRANSCRIPT" ]]; then
            TRANSCRIPT_PATH="$PAYLOAD_TRANSCRIPT"
        else
            # Fallback slug (same formula as memory-reflect-stop.sh): '/' and
            # '.' both become '-', with NO extra leading '-' — the path is
            # absolute, so `tr` already supplies the leading one. The old extra
            # prefix wrote '--Users-...' into every marker, a path matching no
            # directory Claude Code creates, so no marker was ever resolvable
            # and the orphan sweeper skipped all of them forever (200
            # accumulated before this was found).
            CWD_SLUG="$(printf '%s' "$SESSION_CWD" | tr '/.' '--')"
            TRANSCRIPT_PATH="$HOME/.claude/projects/${CWD_SLUG}/${SESSION_ID}.jsonl"
        fi
        # Ensure .harness/ exists; if not, create it (operator may not have
        # initialized the harness in this project yet — marker is still useful
        # to write, even if it gets ignored by other tooling).
        mkdir -p .harness 2>/dev/null
        MARKER=".harness/session-id-${SESSION_ID}.start"
        # Write only if not present already (idempotent; SessionStart fires
        # multiple times per session in resume/clear/compact scenarios).
        #
        # `source:` records which of those fired (startup / resume / clear /
        # compact / fork). Nothing reads it — the idle hook and
        # orchestration_phase.py both key on `transcript:` alone — but it is
        # the one field that makes an odd marker diagnosable instead of
        # mysterious, and it costs a line.
        if [[ ! -f "$MARKER" ]]; then
            cat > "$MARKER" 2>/dev/null << MARKER_EOF || true
session_id: ${SESSION_ID}
started_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
source: ${SESSION_SOURCE:-unknown}
transcript: ${TRANSCRIPT_PATH}
MARKER_EOF
        fi
    fi
fi

# ── Recall pass ────────────────────────────────────────────────────────────
# Resolve recall.py across install scopes. Memory skill is agentm-native
# (harness/skills/memory/). Install can be project-scope
# (<cwd>/.claude/skills/memory/...), user-scope (~/.claude/skills/memory/...,
# default since agentm v4.3.0 — a symlink into the agentm clone under source
# mode), or a source-mode clone fallback (path recorded in .agentm-config.json's
# source_clones.agentm → <clone>/harness/skills/memory/). Pre-fix, this hook
# hardcoded the project-scope relative path and silently no-op'd on user-scope.
_resolve_memory_script() {  # $1 = script basename (e.g. recall.py)
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

RECALL_PY="$(_resolve_memory_script recall.py 2>/dev/null)" || RECALL_PY=""
if [[ -z "$RECALL_PY" ]]; then
    # Memory skill not installed under any known layout; graceful-skip.
    exit 0
fi

# Resolve MEMORY_VAULT_PATH: env → .agentm-config.json vault_path → none.
# recall.py requires this; Claude Code doesn't inject it into hook env, so
# pre-fix the hook found the script but recall.py silently exited 0 on
# "no vault configured".
_resolve_vault_path() {
    if [[ -n "${MEMORY_VAULT_PATH:-}" ]]; then
        printf '%s\n' "$MEMORY_VAULT_PATH"; return 0
    fi
    local cfg="${AGENTM_INSTALL_PREFIX:-$HOME/.claude}/.agentm-config.json"
    if [[ -f "$cfg" ]] && command -v python3 >/dev/null 2>&1; then
        local v
        v="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print(d.get("plugins.obsidian-vault.vault_path") or d.get("vault_path") or "")
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

# Require python3 — exit 0 if missing (graceful-skip; no blocking).
if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# Resolve the interpreter that runs the memory scripts.
#
# Pre-fix, every memory hook ended in a bare `python3 …`, which is a PATH
# lookup. On a stock macOS box that resolves to Apple's /usr/bin/python3, whose
# sqlite3 is built without `--enable-loadable-sqlite-extensions` and therefore
# has no `enable_load_extension` at all. sqlite-vec is a loadable native
# extension, so `vec_index._open_index()` returned None on every call and every
# caller read that as the graceful "index not built yet" skip — semantic recall
# was structurally unreachable, silently, against a fully healthy 1035-row
# index. See harness/hooks/lib/resolve-python.sh for the resolution order and
# the reasoning behind each step; it is the canonical resolver, and
# scripts/machinery_doctor.py's `memory-hook-interpreter` check delegates to
# the same script so the diagnosis cannot drift from the behavior.
#
# The lib path is resolved relative to this hook file, which is identical in
# the repo (harness/hooks/<name>/ → harness/hooks/lib/) and in an install
# (<prefix>/hooks/<name>/ → <prefix>/hooks/lib/), so no config lookup is
# needed to find it. Falls back to bare `python3` — the pre-fix behavior — if
# the lib is missing, so a partial install degrades exactly as it used to.
#
# Note the ordering: this deliberately sits *after* the `command -v python3`
# gate and the config reads above, which stay on bare `python3`. Those only
# parse small JSON files, which any interpreter can do; only the memory
# scripts need the capable one, and bootstrapping the resolver off the config
# would be circular.
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

# Invoke recall. recall.py handles MEMORY_VAULT_PATH resolution, glob,
# frontmatter parse, filter, output, and the 500ms time budget internally.
# We no longer `exec` it — the SessionStart pending-state briefing
# (orchestration_briefing.py) appends after the always-load recall
# (V4 #23 task 3, DC-3: agentm-native, non-blocking).
"$AGENTM_PY" "$RECALL_PY" session-start || true

# ── Pending-state briefing pass (V4 #23 task 3) ────────────────────────────
# Best-effort, non-blocking: scans the vault for over-threshold pending signals
# (inbox / HIGH skill-watchlist / incubator / stale idea-ledger) and appends a
# tight briefing block — but ONLY when something shifted since last shown AND
# the cooldown allows (anti-fatigue guard in auto_orchestration.py). The
# generator swallows any error → empty output, so this never blocks session
# boot. orchestration_briefing.py is a sibling of recall.py in the same memory
# scripts dir, so the same resolver finds it across install scopes.
BRIEFING_PY="$(_resolve_memory_script orchestration_briefing.py 2>/dev/null)" || BRIEFING_PY=""
if [[ -n "$BRIEFING_PY" ]]; then
    if [[ -n "${MEMORY_VAULT_PATH:-}" ]]; then
        "$AGENTM_PY" "$BRIEFING_PY" --vault-path "$MEMORY_VAULT_PATH" 2>/dev/null || true
    else
        "$AGENTM_PY" "$BRIEFING_PY" 2>/dev/null || true
    fi
fi

exit 0
