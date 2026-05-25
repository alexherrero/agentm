# PreCompact hook for agentm (PowerShell twin of precompact.sh).
#
# Fires before Claude Code compacts the conversation (manual /compact or
# auto). Appends a "compaction event" marker to .harness/progress.md so
# the post-compaction session has a clear anchor point in the durable
# state file.
#
# Pure side-effect. Never blocks compaction (always exits 0).

$ErrorActionPreference = 'SilentlyContinue'

$progress = Join-Path '.harness' 'progress.md'
if (-not (Test-Path -LiteralPath $progress)) { exit 0 }

$stdin = [Console]::In.ReadToEnd()
$trigger = 'unknown'
$custom = ''
if ($stdin) {
    try {
        $obj = $stdin | ConvertFrom-Json -ErrorAction Stop
        if ($obj.trigger) { $trigger = [string]$obj.trigger }
        if ($obj.custom_instructions) { $custom = [string]$obj.custom_instructions }
    } catch { }
}

$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
if (-not $branch -or $LASTEXITCODE -ne 0) { $branch = 'unknown' }

$lines = @(
    ''
    "## compaction event — $ts"
    "- trigger: $trigger"
    "- branch: $branch"
)
if ($custom) { $lines += "- /compact instructions: $custom" }
$lines += @(
    '- The session was compacted at this point. To re-anchor on the'
    '  in-flight task, read .harness/PLAN.md and the entries above'
    '  this marker. The compaction summary alone is not enough.'
)

Add-Content -LiteralPath $progress -Value $lines

exit 0
