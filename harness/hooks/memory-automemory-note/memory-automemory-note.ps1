# memory-automemory-note.ps1 — Windows twin of memory-automemory-note.sh.
#
# A PostToolUse hook on Write, Edit and MultiEdit: a note saved under
# ~/.claude/projects/<folder>/memory/ is recorded for the night's lesson compile
# (task 199). Nothing is filed from here. Every error means "carry on".

try {
    $payload = [Console]::In.ReadToEnd()
} catch {
    exit 0
}
if (-not $payload) { exit 0 }
$norm = $payload -replace '\\\\', '/'
if (-not ($norm -like '*/.claude/projects/*/memory/*')) { exit 0 }

$prefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { Join-Path $HOME '.claude' }
$script = Join-Path $prefix 'skills/memory/scripts/lesson_compile.py'
if (-not (Test-Path -LiteralPath $script)) {
    $script = Join-Path $PSScriptRoot '../../skills/memory/scripts/lesson_compile.py'
    if (-not (Test-Path -LiteralPath $script)) { exit 0 }
}

$python = $null
foreach ($name in @('python3', 'python')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) { exit 0 }

$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    $payload | & $python $script note-automemory | Out-Null
} catch {}
exit 0
