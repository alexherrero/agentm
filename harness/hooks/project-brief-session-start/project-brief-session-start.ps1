#!/usr/bin/env pwsh
# project-brief-session-start (pwsh twin) — the opening brief for the project and
# task a session is bound to (agentm-vault § Projects and tasks). Mirrors
# project-brief-session-start.sh: the brief when a tracker exists, else today's
# plan block, rendered by harness-context-session-start with
# AGENTM_PLAN_BLOCK_ONLY=1. Never blocks boot.

$ErrorActionPreference = 'SilentlyContinue'

$python = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    [Console]::Error.WriteLine("[project-brief] python unavailable — skipped")
    exit 0
}
$py = $python.Source

# ── The session's directory, from the event (not the process cwd) ──
$payload = [Console]::In.ReadToEnd()
$eventCwd = ""
if ($payload) {
    try { $eventCwd = ([string](($payload | ConvertFrom-Json).cwd)) } catch { $eventCwd = "" }
}
if (-not $eventCwd) { $eventCwd = (Get-Location).Path }

# ── Resolve project_brief.py: recorded agentm source clone → fallback ──
$HomeDir = if ($env:HOME) { $env:HOME } else { $HOME }
$brief = ""
$cfg = Join-Path $HomeDir ".claude/.agentm-config.json"
if (Test-Path -LiteralPath $cfg) {
    try {
        $clone = [string]((Get-Content -Raw -LiteralPath $cfg | ConvertFrom-Json).source_clones.agentm)
        if ($clone) {
            $cand = Join-Path $clone "scripts/project_brief.py"
            if (Test-Path -LiteralPath $cand) { $brief = $cand }
        }
    } catch { }
}
if (-not $brief) {
    $fallback = Join-Path $HomeDir "Antigravity/agentm/scripts/project_brief.py"
    if (Test-Path -LiteralPath $fallback) { $brief = $fallback }
}

# ── The brief, when a tracker exists ──
# UTF-8 end to end, the way the host reads a hook's output. Left alone, Python
# writes a pipe in the ANSI code page and this host decodes it with the console's,
# so the brief's own separators, and whatever its trackers and progress say, come
# out as replacement characters. Both are put back before the fallback, so the
# plan block prints exactly as the context hook prints it on its own.
if ($brief -and (Test-Path -LiteralPath $eventCwd -PathType Container)) {
    $savedIo = $env:PYTHONIOENCODING
    $savedOut = $null
    try {
        $savedOut = [Console]::OutputEncoding
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    } catch { }
    $env:PYTHONIOENCODING = 'utf-8'
    $out = & $py $brief --cwd $eventCwd 2>$null
    $briefExit = $LASTEXITCODE
    $env:PYTHONIOENCODING = $savedIo
    if ($briefExit -eq 0 -and $out) {
        @($out) | ForEach-Object { Write-Output $_ }
        [Console]::Error.WriteLine("[project-brief] brief for $eventCwd")
        exit 0
    }
    if ($savedOut) {
        try { [Console]::OutputEncoding = $savedOut } catch { }
    }
}

# ── No brief: today's plan block, rendered by the context hook itself ──
$contextHook = Join-Path (Split-Path $PSScriptRoot -Parent) "harness-context-session-start/harness-context-session-start.ps1"
if (Test-Path -LiteralPath $contextHook) {
    $env:AGENTM_PLAN_BLOCK_ONLY = '1'
    $payload | & (Get-Process -Id $PID).Path -NoProfile -File $contextHook
} else {
    [Console]::Error.WriteLine("[project-brief] no brief, and no context hook beside this one — skipped")
}
exit 0
