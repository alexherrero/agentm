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

# ── Crash-recovery marker (plan #7a part 3 task 6) ─────────────────────────
# Parse SessionStart event's stdin JSON for transcript_path, session_id, cwd
# and source; write a .harness/session-id-<sid>.start marker so the idle hook's
# orphan-recovery sweep can detect crashed sessions. Marker write is best-effort.
$Payload = ($Input | Out-String).Trim()
if ($Payload) {
    $ParseDriver = @"
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get('session_id') or ''
cwd = d.get('cwd') or ''
tp = d.get('transcript_path') or ''
src = d.get('source') or ''
if sid:
    print(f'{sid}\t{cwd}\t{tp}\t{src}')
"@
    $Parsed = ($Payload | & $Py -c $ParseDriver 2>$null).Trim()
    if ($Parsed) {
        $Parts = $Parsed -split "`t"
        $SessionId = $Parts[0]
        $Cwd = if ($Parts.Length -gt 1 -and $Parts[1]) { $Parts[1] } else { (Get-Location).Path }
        $PayloadTranscript = if ($Parts.Length -gt 2) { $Parts[2] } else { "" }
        $SessionSource = if ($Parts.Length -gt 3 -and $Parts[3]) { $Parts[3] } else { "unknown" }
        # Transcript path: the payload's own `transcript_path` is authoritative;
        # the computed slug is the fallback for hosts too old to send it. See
        # memory-reflect-stop.ps1 for the rationale, and the bash siblings for
        # the full account.
        if ($PayloadTranscript) {
            $TranscriptPath = $PayloadTranscript
        } else {
            # Fallback slug (same formula as memory-reflect-stop.ps1; strip ':' for Windows).
            $CwdSlug = "-" + (($Cwd -replace '[\\/]', '-') -replace ':', '')
            # $HOME (automatic variable) is USERPROFILE-derived on Windows and does
            # not follow a HOME env var set on the process; $env:HOME is a direct
            # env-var read on every platform, so prefer it and fall back to $HOME
            # for real end-user machines that don't set HOME at all.
            $HomeDir = if ($env:HOME) { $env:HOME } else { $HOME }
            $TranscriptPath = Join-Path $HomeDir ".claude/projects/$CwdSlug/$SessionId.jsonl"
        }
        # Ensure .harness/ exists.
        $HarnessDir = ".harness"
        if (-not (Test-Path $HarnessDir)) {
            New-Item -ItemType Directory -Path $HarnessDir -Force -ErrorAction SilentlyContinue | Out-Null
        }
        $Marker = Join-Path $HarnessDir "session-id-$SessionId.start"
        if (-not (Test-Path $Marker)) {
            $Now = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
            # `source:` records which SessionStart fired (startup / resume /
            # clear / compact / fork). Nothing reads it; it is there to make an
            # odd marker diagnosable. Matches the bash sibling's field order.
            $MarkerContent = @"
session_id: $SessionId
started_at: $Now
source: $SessionSource
transcript: $TranscriptPath
"@
            try {
                Set-Content -LiteralPath $Marker -Value $MarkerContent -ErrorAction SilentlyContinue
            } catch {}
        }
    }
}

# ── Recall pass ────────────────────────────────────────────────────────────
$RecallPy = ".claude/skills/memory/scripts/recall.py"
if (-not (Test-Path $RecallPy)) {
    exit 0
}

# Recall is no longer the terminal step — the pending-state briefing appends
# after the always-load recall (V4 #23 task 3, DC-3: non-blocking).
& $Py $RecallPy session-start

# ── Pending-state briefing pass (V4 #23 task 3) ────────────────────────────
# Best-effort, non-blocking: scans the vault for over-threshold pending signals
# and appends a tight briefing block — but ONLY when something shifted since
# last shown AND the cooldown allows. The generator swallows any error → empty
# output, so this never blocks session boot. orchestration_briefing.py is a
# sibling of recall.py in the same memory scripts dir.
$BriefingPy = ".claude/skills/memory/scripts/orchestration_briefing.py"
if (Test-Path $BriefingPy) {
    try {
        if ($env:MEMORY_VAULT_PATH) {
            & $Py $BriefingPy --vault-path $env:MEMORY_VAULT_PATH 2>$null
        } else {
            & $Py $BriefingPy 2>$null
        }
    } catch {}
}
exit 0
