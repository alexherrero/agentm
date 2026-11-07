# memory-reflect-stop — keep the session's episodic trace current on Stop (Windows / pwsh).
# Mirrors memory-reflect-stop.sh. It mines nothing: lessons are compiled at
# night from every session's new slice (lesson_compile.py, task 199).
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern; hook
# must never block session end.

$TracePy = ".claude/skills/memory/scripts/episodic_trace.py"
if (-not (Test-Path $TracePy)) {
    exit 0
}

if (-not (Get-Command python3 -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue)) {
    exit 0
}

# Resolve the interpreter that runs the memory scripts. See
# memory-recall-session-start.ps1 for the rationale + bug history.
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

# Read stdin (Stop event JSON payload). PowerShell forwards stdin via $Input.
$Payload = ($Input | Out-String).Trim()
if (-not $Payload) {
    [Console]::Error.WriteLine("[memory-reflect-stop] no stdin payload (skipping)")
    exit 0
}

# Parse session_id + cwd + transcript_path via embedded Python (consistent with
# the bash side).
$ParseDriver = @"
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get('session_id') or ''
cwd = d.get('cwd') or ''
tp = d.get('transcript_path') or ''
if sid:
    print(f'{sid}\t{cwd}\t{tp}')
"@
$Parsed = ($Payload | & $Py -c $ParseDriver 2>$null).Trim()
if (-not $Parsed) {
    [Console]::Error.WriteLine("[memory-reflect-stop] no session_id on stdin (skipping)")
    exit 0
}

$Parts = $Parsed -split "`t"
$SessionId = $Parts[0]
$Cwd = if ($Parts.Length -gt 1 -and $Parts[1]) { $Parts[1] } else { (Get-Location).Path }
$PayloadTranscript = if ($Parts.Length -gt 2) { $Parts[2] } else { "" }

# Transcript path: the payload's own `transcript_path` is authoritative. Claude
# Code sends it on every hook event, and the session-id → filename
# correspondence it replaces is not guaranteed. The computed path below stays
# as a fallback for hosts too old to send the field — see the bash sibling for
# the full rationale.
#
# The fallback matters more here than on POSIX: the slug convention Claude Code
# uses on Windows was never confirmed, so this branch was always a best guess.
# A payload-supplied path retires that guess outright.
if ($PayloadTranscript) {
    $Transcript = $PayloadTranscript
} else {
    # Fallback slug: replace path separators + drive-letter colons with '-' +
    # leading '-'. The ':' replacement is Windows-specific — drive letters like
    # "C:" produce invalid filename chars otherwise. If the real convention
    # differs, the hook gracefully misses the transcript (exit 0 + "transcript
    # not found").
    $CwdSlug = "-" + (($Cwd -replace '[\\/]', '-') -replace ':', '')
    # $HOME (automatic variable) is USERPROFILE-derived on Windows and does not
    # follow a HOME env var set on the process; $env:HOME is a direct env-var
    # read on every platform, so prefer it and fall back to $HOME for real
    # end-user machines that don't set HOME at all.
    $HomeDir = if ($env:HOME) { $env:HOME } else { $HOME }
    $Transcript = Join-Path $HomeDir ".claude/projects/$CwdSlug/$SessionId.jsonl"
}

# A payload-supplied path that does not exist is reported and skipped, never
# quietly retried against the computed one.
if (-not (Test-Path $Transcript)) {
    [Console]::Error.WriteLine("[memory-reflect-stop] transcript not found: $Transcript (skipping)")
    exit 0
}

# Filing v2 remainders task 7: the session's episodic trace — what it captured
# and recalled, as links under memory/episodic/ — rewritten on every Stop, so
# every turn reaches it. Best-effort: a trace never blocks session end.
try { & $Py $TracePy $Transcript "--session" $SessionId "--surface" "claude-code" 2>$null | Out-Null } catch { }

exit 0
