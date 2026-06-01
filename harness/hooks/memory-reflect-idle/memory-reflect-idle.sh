#!/usr/bin/env bash
# memory-reflect-idle — orphan-recovery + idle reflection sweep.
#
# Fires on Claude Code's SessionStart event + invokable manually / via cron.
# Scans .harness/session-id-*.start markers for orphans (markers older than
# the idle threshold = crashed sessions where Stop didn't fire); runs
# reflection retroactively on each orphan's transcript; renames .start →
# .reflected on success. GCs .reflected markers older than 30 days.
#
# Plan #7a part 3 task 4 — new crickets primitive. Markers themselves
# are written by task 6 (SessionStart + Stop extensions).
#
# See hook.md in this directory for full documentation.

set -uo pipefail  # NOTE: no -e — graceful-skip pattern; hook must never block session start.

# Resolve memory-skill scripts across install scopes (project → user → source-clone).
# See memory-recall-session-start.sh for the rationale + bug history. This hook
# uses three scripts: reflect.py (orphan-recovery), discover_skills.py (V4 #37),
# and vec_index.py (V4 #37). All three resolve through the same helper.
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
print((d.get("source_clones") or {}).get("crickets") or "")
' "$cfg" 2>/dev/null || true)"
        if [[ -n "$clone" && -f "$clone/skills/memory/scripts/$script" ]]; then
            printf '%s\n' "$clone/skills/memory/scripts/$script"; return 0
        fi
    fi
    return 1
}

REFLECT_PY="$(_resolve_memory_script reflect.py 2>/dev/null)" || REFLECT_PY=""
if [[ -z "$REFLECT_PY" ]]; then
    exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
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
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print(d.get("vault_path") or "")
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

# Idle threshold: 1 hour (3600s) per locked design call B2.ii. Override via env.
IDLE_THRESHOLD_SEC="${MEMORY_IDLE_THRESHOLD_SEC:-3600}"
# GC threshold for .reflected markers: 30 days.
GC_THRESHOLD_SEC="${MEMORY_REFLECTED_GC_SEC:-2592000}"

# Glob session-id-*.start markers. shopt nullglob makes the array empty if
# no matches (vs. bash's default of leaving the literal pattern).
shopt -s nullglob
markers=(.harness/session-id-*.start)
shopt -u nullglob

# Also collect .reflected markers for the GC pass.
shopt -s nullglob
reflected_markers=(.harness/session-id-*.reflected)
shopt -u nullglob

if [[ ${#markers[@]} -eq 0 && ${#reflected_markers[@]} -eq 0 ]]; then
    # No orphan work to do — but the skill-discovery scan still runs.
    # Fall through to the bottom of the script (discover-skills block + exit).
    no_orphan_work=1
else
    no_orphan_work=0
fi

# Portable mtime via Python (we already require python3 above). Avoids the
# stat(1) GNU-vs-BSD `-c` / `-f` flag mismatch (the original implementation
# tried `stat -f %m` first → on Linux GNU stat treats `-f` as filesystem-info
# and silently returned the wrong value, breaking orphan detection).
get_mtime() {
    python3 -c "import os, sys; print(int(os.path.getmtime(sys.argv[1])))" "$1" 2>/dev/null || echo 0
}

now=$(date +%s)
processed_count=0

if (( no_orphan_work == 1 )); then
    # Skip the orphan + GC passes entirely; fall through to discover-skills.
    :
fi

for marker in "${markers[@]:-}"; do
    [[ -n "$marker" && -f "$marker" ]] || continue
    mtime=$(get_mtime "$marker")
    age_sec=$((now - mtime))
    if (( age_sec < IDLE_THRESHOLD_SEC )); then
        # Marker is fresh; session might still be active. Skip.
        continue
    fi

    # Parse marker for transcript path. Format (locked in task 6):
    #   session_id: <uuid>
    #   started_at: <iso-timestamp>
    #   transcript: <absolute-path>
    transcript="$(grep '^transcript:' "$marker" 2>/dev/null | head -1 | sed 's/^transcript:[[:space:]]*//')"
    if [[ -z "$transcript" ]]; then
        echo "[memory-reflect-idle] marker $marker missing 'transcript:' line (skipping)" >&2
        continue
    fi
    if [[ ! -f "$transcript" ]]; then
        echo "[memory-reflect-idle] marker $marker transcript not found: $transcript (skipping)" >&2
        continue
    fi

    # Run reflection with --route (HIGH → canonical / MEDIUM+LOW → _inbox/
    # via reflect.py's tri-modal routing). Requires MEMORY_VAULT_PATH; if
    # unset, --route fails non-zero + marker stays .start for next pass.
    if python3 "$REFLECT_PY" "$transcript" --summary --route 2>/dev/null; then
        # Rename .start → .reflected on success.
        mv "$marker" "${marker%.start}.reflected" 2>/dev/null && processed_count=$((processed_count + 1))
    fi
done

# GC pass: delete .reflected markers older than 30 days.
# Guard the array expansion with #count check so `set -u` doesn't error on
# an empty nullglob result (bash 4.x quirk).
gc_count=0
if (( ${#reflected_markers[@]} > 0 )); then
    for reflected in "${reflected_markers[@]}"; do
        [[ -f "$reflected" ]] || continue
        mtime=$(get_mtime "$reflected")
        age_sec=$((now - mtime))
        if (( age_sec > GC_THRESHOLD_SEC )); then
            rm -f "$reflected" && gc_count=$((gc_count + 1))
        fi
    done
fi

if (( ${#markers[@]} > 0 || gc_count > 0 )); then
    echo "[memory-reflect-idle] Scanned ${#markers[@]} .start + ${#reflected_markers[@]} .reflected markers; processed $processed_count orphans, GC'd $gc_count old markers (idle threshold: ${IDLE_THRESHOLD_SEC}s)" >&2
fi

# ── Idle orchestration chain (V4 #23 task 4) ──────────────────────────────
# Fire the cooldown-gated chain driver: reflect-corpus (≤5 unseen sessions) →
# discover-skills (cadence-checked) → adapt_skills Pass-1 (≤3 candidates
# staged for the adapt-evaluator). The driver self-gates on the idle_chain
# cooldown (default 24h) + the enable_idle_chain toggle, so most invocations
# are a fast no-op; when it DOES fire it can take longer than this hook's 30s
# SessionStart timeout (corpus mining + GitHub enrichment), so we run it
# DETACHED — a subshell background job, reparented away from the hook — and
# return immediately. Killing the hook at 30s would otherwise leave the chain
# never recording its fire, re-running every session. The chain's results
# surface on the NEXT session via the task-3 briefing. Requires
# MEMORY_VAULT_PATH; graceful-skip if unset / driver absent.
ORCH_IDLE_PY="$(_resolve_memory_script orchestration_idle.py 2>/dev/null)" || ORCH_IDLE_PY=""
if [[ -n "$ORCH_IDLE_PY" && -n "${MEMORY_VAULT_PATH:-}" ]]; then
    ( python3 "$ORCH_IDLE_PY" --vault-path "$MEMORY_VAULT_PATH" >/dev/null 2>&1 & ) 2>/dev/null || true
fi

# ── Vec-index drift sweep (V4 #37 task 6) ─────────────────────────────────
# Fire vec_index.py full-sync (read-only; no --rebuild) so the operator
# sees drift accumulation without surprise. Requires MEMORY_VAULT_PATH;
# graceful-skip if unset / vec_index.py absent / sqlite-vec unavailable.
#
# Sub-second overhead at typical vault size (one os.stat per entry + one
# bulk sqlite query). Non-blocking — surfaces drift count to stderr per
# the existing idle-pass transparency convention. Operators with drift
# accumulation run `vec_index.py full-sync --rebuild` to enqueue.
VEC_INDEX_PY="$(_resolve_memory_script vec_index.py 2>/dev/null)" || VEC_INDEX_PY=""
if [[ -n "$VEC_INDEX_PY" && -n "${MEMORY_VAULT_PATH:-}" ]]; then
    drift_json=$(python3 "$VEC_INDEX_PY" --vault-path "$MEMORY_VAULT_PATH" full-sync 2>/dev/null || echo '{}')
    if [[ -n "$drift_json" ]]; then
        drift_summary=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    drifted = d.get('drifted_count', 0)
    not_indexed = d.get('not_indexed_count', 0)
    if drifted or not_indexed:
        print(f'{drifted} drifted + {not_indexed} not-indexed')
except Exception:
    pass
" "$drift_json" 2>/dev/null)
        if [[ -n "$drift_summary" ]]; then
            echo "[memory-reflect-idle] vec-index drift sweep: $drift_summary (run \`python3 $VEC_INDEX_PY full-sync --rebuild\` to enqueue for re-embed)" >&2
        fi
    fi
fi

exit 0
