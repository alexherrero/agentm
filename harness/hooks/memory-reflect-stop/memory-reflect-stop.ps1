# memory-reflect-stop — mine the just-ended session's transcript on Stop (Windows / pwsh).
# Mirrors memory-reflect-stop.sh.
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern; hook
# must never block session end.

$RecallPy = $null  # Unused; kept for symmetry with recall hooks.
$ReflectPy = ".claude/skills/memory/scripts/reflect.py"
if (-not (Test-Path $ReflectPy)) {
    exit 0
}

if (-not (Get-Command python3 -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue)) {
    exit 0
}

$Py = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }

# Read stdin (Stop event JSON payload). PowerShell forwards stdin via $Input.
$Payload = ($Input | Out-String).Trim()
if (-not $Payload) {
    [Console]::Error.WriteLine("[memory-reflect-stop] no stdin payload (skipping)")
    exit 0
}

# Parse session_id + cwd via embedded Python (consistent with the bash side).
$ParseDriver = @"
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
sid = d.get('session_id') or ''
cwd = d.get('cwd') or ''
if sid:
    print(f'{sid}\t{cwd}')
"@
$Parsed = ($Payload | & $Py -c $ParseDriver 2>$null).Trim()
if (-not $Parsed) {
    [Console]::Error.WriteLine("[memory-reflect-stop] no session_id on stdin (skipping)")
    exit 0
}

$Parts = $Parsed -split "`t"
$SessionId = $Parts[0]
$Cwd = if ($Parts.Length -gt 1 -and $Parts[1]) { $Parts[1] } else { (Get-Location).Path }

# Compute transcript path: ~/.claude/projects/<cwd-slug>/<session_id>.jsonl
# CWD slug: replace path separators + drive-letter colons with '-' + leading '-'.
# The ':' replacement is Windows-specific — drive letters like "C:" produce
# invalid filename chars otherwise. The exact slug convention Claude Code uses
# on Windows may differ from this; if it does, the hook gracefully misses the
# transcript (exit 0 + "transcript not found"). Tracked as a Windows-recall
# follow-up if real-world dogfood surfaces a mismatch.
$CwdSlug = "-" + (($Cwd -replace '[\\/]', '-') -replace ':', '')
# $HOME (automatic variable) is USERPROFILE-derived on Windows and does not
# follow a HOME env var set on the process; $env:HOME is a direct env-var
# read on every platform, so prefer it and fall back to $HOME for real
# end-user machines that don't set HOME at all.
$HomeDir = if ($env:HOME) { $env:HOME } else { $HOME }
$Transcript = Join-Path $HomeDir ".claude/projects/$CwdSlug/$SessionId.jsonl"

if (-not (Test-Path $Transcript)) {
    [Console]::Error.WriteLine("[memory-reflect-stop] transcript not found: $Transcript (skipping)")
    exit 0
}

# ── Phase-dispatch dedup guard (V4 #23 task 5) ─────────────────────────────
# If a .reflected marker for this session already exists, the post-/work
# phase-integration dispatch (orchestration_phase.py) already reflected this
# transcript. Re-reflecting would double-route (HIGH save errors on a slug
# collision), so skip — they cooperate via this marker.
$ReflectedGuard = Join-Path ".harness" "session-id-$SessionId.reflected"
if (Test-Path $ReflectedGuard) {
    [Console]::Error.WriteLine("[memory-reflect-stop] session $SessionId already reflected (phase dispatch); skipping")
    exit 0
}

# Invoke reflect.py with --summary + --route. Captured once so we can
# reuse for transparency line + stdout pass-through (running --route
# twice would error on slug collision for HIGH saves).
$ReflectArgs = @($ReflectPy, $Transcript, "--summary", "--route")
$ReflectOut = & $Py @ReflectArgs 2>&1 | Out-String
$ReflectExit = $LASTEXITCODE
if ($ReflectExit -ne 0) {
    [Console]::Error.WriteLine("[memory-reflect-stop] reflect.py --route exited $ReflectExit (MEMORY_VAULT_PATH set?); transcript was $Transcript")
    exit 0
}

# Parse summary + route lines.
$SummaryLine = ($ReflectOut -split "`n" | Where-Object { $_ -match '"pass": "summary"' } | Select-Object -First 1)
$RouteLine = ($ReflectOut -split "`n" | Where-Object { $_ -match '"pass": "route"' } | Select-Object -First 1)

$CountDriver = @"
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('memory_candidate_count', 0))
    print(d.get('idea_candidate_count', 0))
except Exception:
    print(0)
    print(0)
"@
$Counts = if ($SummaryLine) { ($SummaryLine | & $Py -c $CountDriver 2>$null) -split "`n" } else { @('0', '0') }
$MemCount = if ($Counts.Length -ge 1) { $Counts[0] } else { '0' }
$IdeaCount = if ($Counts.Length -ge 2) { $Counts[1] } else { '0' }

if ($RouteLine) {
    $RouteCountDriver = @"
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('auto_saved', 0) + d.get('approved', 0))
    print(d.get('inboxed', 0) + d.get('ideas_inboxed', 0))
except Exception:
    print(0)
    print(0)
"@
    $RouteCounts = ($RouteLine | & $Py -c $RouteCountDriver 2>$null) -split "`n"
    $Saved = if ($RouteCounts.Length -ge 1) { $RouteCounts[0] } else { '0' }
    $Inboxed = if ($RouteCounts.Length -ge 2) { $RouteCounts[1] } else { '0' }
    [Console]::Error.WriteLine("[memory-reflect-stop] Mined $MemCount memory + $IdeaCount idea candidates from $Transcript; saved $Saved, inboxed $Inboxed")
} else {
    [Console]::Error.WriteLine("[memory-reflect-stop] Mined $MemCount memory + $IdeaCount idea candidates from $Transcript (routing skipped)")
}

# Pass through captured reflect.py output on stdout.
Write-Output $ReflectOut

# ── Crash-recovery marker rename (plan #7a part 3 task 6) ──────────────────
# Reflection succeeded → rename .harness/session-id-<sid>.start → .reflected.
$Marker = Join-Path ".harness" "session-id-$SessionId.start"
if (Test-Path $Marker) {
    $ReflectedMarker = $Marker -replace '\.start$', '.reflected'
    try {
        Move-Item -LiteralPath $Marker -Destination $ReflectedMarker -Force -ErrorAction SilentlyContinue
    } catch {}
}

exit 0
