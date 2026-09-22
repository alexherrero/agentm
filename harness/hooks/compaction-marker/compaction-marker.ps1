# compaction-marker.ps1 — Windows twin of compaction-marker.sh.
#
# A PreCompact hook, registered once for the machine. Leaves a dated marker in
# the project's progress log so entries above it read as "written before the
# context was lost" rather than as one continuous history.
#
# See compaction-marker.sh for the full reasoning — in particular why the
# marker goes through `append-progress`, which resolves the active plan's log
# and writes through the storage backend, rather than being appended directly.

$ErrorActionPreference = 'Continue'   # never block a compaction

try { $payload = [Console]::In.ReadToEnd() } catch { exit 0 }

$py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { exit 0 }

# DC-6: the event's cwd, not $PWD.
$eventCwd = $null; $trigger = 'unknown'; $custom = ''
if ($payload) {
    try {
        $d = $payload | ConvertFrom-Json
        if ($d.cwd) { $eventCwd = $d.cwd }
        if ($d.trigger) { $trigger = $d.trigger }
        if ($d.custom_instructions) { $custom = $d.custom_instructions }
    } catch { }
}
if (-not $eventCwd) { $eventCwd = (Get-Location).Path }
if (-not (Test-Path -LiteralPath $eventCwd -PathType Container)) { exit 0 }

# Resolve harness_memory.py: recorded agentm source clone -> fallback.
$resolver = $null
$cfg = Join-Path $HOME '.claude/.agentm-config.json'
if (Test-Path -LiteralPath $cfg) {
    try {
        $c = Get-Content -Raw -LiteralPath $cfg | ConvertFrom-Json
        $clone = $c.source_clones.agentm
        if ($clone) {
            $cand = Join-Path $clone 'scripts/harness_memory.py'
            if (Test-Path -LiteralPath $cand) { $resolver = $cand }
        }
    } catch { }
}
if (-not $resolver) {
    $cand = Join-Path $HOME 'Antigravity/agentm/scripts/harness_memory.py'
    if (Test-Path -LiteralPath $cand) { $resolver = $cand }
}
if (-not $resolver) { exit 0 }

$ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$branch = 'unknown'
try {
    # "HEAD" means unborn or detached, not a branch name worth recording —
    # the bash twin drops it for the same reason.
    $b = & git -C $eventCwd rev-parse --abbrev-ref HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $b -and $b.Trim() -ne 'HEAD') { $branch = $b.Trim() }
} catch { }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('')
$lines.Add("## compaction event — $ts")
$lines.Add("- trigger: $trigger")
$lines.Add("- branch: $branch")
if ($custom) { $lines.Add("- /compact instructions: $custom") }
$lines.Add('- The session was compacted at this point. Entries above this marker')
$lines.Add('  were written before the context was lost; the compaction summary')
$lines.Add('  alone does not carry the per-file specifics /work and /review need.')

$tmp = [System.IO.Path]::GetTempFileName()
try {
    (($lines -join "`n") + "`n") | Set-Content -LiteralPath $tmp -NoNewline
    # append-progress picks the active plan's log (a task's in the vault, or the
    # repo-local pair's) and writes through the storage backend; a bare call on a
    # project that keeps tasks exits 4 and an absent or empty log is left alone.
    & $py.Source $resolver 'append-progress' '--project-root' $eventCwd '--content-file' $tmp *>$null
} finally {
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}

exit 0
