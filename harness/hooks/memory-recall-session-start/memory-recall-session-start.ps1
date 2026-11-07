# memory-recall-session-start — load MemoryVault always-load entries on session boot (Windows / pwsh).
# Mirrors memory-recall-session-start.sh.
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern; hook must
# never block session boot. Errors are caught + swallowed inline.

# Require python3 — exit 0 if missing.
if (-not (Get-Command python3 -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue)) {
    exit 0
}
# Resolve the interpreter that runs the memory scripts.
#
# Pre-fix, every memory hook picked `python3` (else `python`) by bare name,
# which is a PATH lookup. On macOS that resolves to Apple's system Python,
# whose sqlite3 is built without `--enable-loadable-sqlite-extensions` and so
# has no `enable_load_extension` at all; sqlite-vec is a loadable native
# extension, so the vector index could never open, and every
# caller read that as the graceful "index not built yet" skip. Semantic recall
# was structurally unreachable, silently, against a fully healthy index. The
# property is a build flag, not an OS, so this Windows half probes rather than
# assumes. See ../lib/resolve-python.ps1 for the resolution order; it is the
# canonical resolver, shared with the other three memory hooks.
#
# The lib path is resolved relative to this hook file, which is identical in
# the repo (harness/hooks/<name>/ → harness/hooks/lib/) and in an install
# (<prefix>/hooks/<name>/ → <prefix>/hooks/lib/). Falls back to the pre-fix
# python3-else-python choice if the lib is missing, so a partial install
# degrades exactly as it used to.
function Resolve-AgentmPython {
    $lib = Join-Path $PSScriptRoot '..' 'lib' 'resolve-python.ps1'
    if (Test-Path -LiteralPath $lib) {
        try {
            $resolved = & $lib 2>$null | Select-Object -First 1
            if ($resolved) { return "$resolved".Trim() }
        } catch {
            # Fall through to the floor below.
        }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return 'python3' }
    return 'python'
}
$Py = Resolve-AgentmPython

# The payload is read once, here. This hook wrote a .harness/session-id-<sid>.start
# marker until task 199 moved mining to the night; nothing reads one any more.
$Payload = ($Input | Out-String).Trim()

# Which host fired this? The briefing below is Claude Code's: it records that it
# was shown. Another host (the Antigravity registration sets AGENTM_HOST) does
# not get it. A transcript
# under a .gemini directory is never Claude's either, whatever the environment
# says. The bash sibling carries the full account.
$HostIsClaude = (-not $env:AGENTM_HOST) -or ($env:AGENTM_HOST -ceq 'claude-code')

if ($Payload) {
    $TpDriver = @"
import json, sys
try:
    print((json.loads(sys.stdin.read()) or {}).get('transcript_path') or '')
except Exception:
    pass
"@
    $PayloadTranscript = ($Payload | & $Py -c $TpDriver 2>$null)
    if ("$PayloadTranscript" -match '[\\/]\.gemini[\\/]') { $HostIsClaude = $false }
}

# ── Recall pass ────────────────────────────────────────────────────────────
$RecallPy = ".claude/skills/memory/scripts/recall.py"
if (-not (Test-Path $RecallPy)) {
    exit 0
}

# Recall is no longer the terminal step — the pending-state briefing appends
# after the always-load recall (V4 #23 task 3, DC-3: non-blocking).
# The payload goes along for its `cwd`: a project's recall_excludes rule follows
# the session's folder, which on another host need not be this process's.
$Payload | & $Py $RecallPy session-start --stdin-payload

# ── Pending-state briefing pass (V4 #23 task 3) ────────────────────────────
# Best-effort, non-blocking: scans the vault for over-threshold pending signals
# and appends a tight briefing block — but ONLY when something shifted since
# last shown AND the cooldown allows. The generator swallows any error → empty
# output, so this never blocks session boot. orchestration_briefing.py is a
# sibling of recall.py in the same memory scripts dir.
$BriefingPy = ".claude/skills/memory/scripts/orchestration_briefing.py"
if ($HostIsClaude -and (Test-Path $BriefingPy)) {
    try {
        $VaultEnv = if ($env:MEMORY_ROOT) { $env:MEMORY_ROOT } else { $env:MEMORY_VAULT_PATH }
        if ($VaultEnv) {
            & $Py $BriefingPy --vault-path $VaultEnv 2>$null
        } else {
            & $Py $BriefingPy 2>$null
        }
    } catch {}
}
exit 0
