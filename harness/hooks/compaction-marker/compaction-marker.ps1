# compaction-marker.ps1 — Windows twin of compaction-marker.sh.
#
# A PreCompact hook, registered once for the machine. Leaves a dated marker in
# the project's progress log so entries above it read as "written before the
# context was lost" rather than as one continuous history.
#
# See compaction-marker.sh for the full reasoning — in particular why the
# progress file is resolved through the process seam and written via
# `write-state` (which routes through vault_lock.atomic_write) rather than
# appended to directly.

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

# Which progress file? Ask the seam, so a named plan gets its own log.
$pair = & $py.Source $resolver 'resolve-active-plan' '--project-root' $eventCwd 2>$null
if (-not $pair) { exit 0 }
$progressPath = ($pair -split "`t")[1]
if (-not $progressPath) { exit 0 }
$progressName = Split-Path -Leaf $progressPath
if (-not $progressName) { exit 0 }

$current = & $py.Source $resolver 'read-state' '--project-root' $eventCwd $progressName 2>$null
if (-not $current) { exit 0 }   # nothing to append to -> not an initialized project

$ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$branch = 'unknown'
try {
    # "HEAD" means unborn or detached, not a branch name worth recording —
    # the bash twin drops it for the same reason.
    $b = & git -C $eventCwd rev-parse --abbrev-ref HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $b -and $b.Trim() -ne 'HEAD') { $branch = $b.Trim() }
} catch { }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add(($current -join "`n"))
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
    ($lines -join "`n") | Set-Content -LiteralPath $tmp -NoNewline
    # write-state, not a direct append: it routes through vault_lock.atomic_write.
    & $py.Source $resolver 'write-state' '--project-root' $eventCwd '--content-file' $tmp $progressName *>$null
} finally {
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}

exit 0
