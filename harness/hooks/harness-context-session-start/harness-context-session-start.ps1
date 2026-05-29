#!/usr/bin/env pwsh
# harness-context-session-start (pwsh twin) — inject the project's vault
# PLAN.md/progress.md paths into session context on SessionStart.
# Mirrors harness-context-session-start.sh. Never blocks session boot. V4 #39.

$ErrorActionPreference = 'SilentlyContinue'

function Write-Skip([string]$reason) {
    [Console]::Error.WriteLine("[harness-context] $reason — skipped")
    exit 0
}

$python = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { Write-Skip "python unavailable" }
$py = $python.Source

# ── Read SessionStart event JSON from stdin; extract cwd (DC-6) ──
$payload = [Console]::In.ReadToEnd()
$eventCwd = ""
if ($payload) {
    try { $eventCwd = ([string](($payload | ConvertFrom-Json).cwd)) } catch { $eventCwd = "" }
}
if (-not $eventCwd) { $eventCwd = (Get-Location).Path }
if (-not (Test-Path -LiteralPath $eventCwd -PathType Container)) { Write-Skip "event cwd not a directory" }

# ── Resolve harness_memory.py: recorded agentm source clone → fallback ──
$resolver = ""
$cfg = Join-Path $HOME ".claude/.agentm-config.json"
if (Test-Path -LiteralPath $cfg) {
    try {
        $clone = [string]((Get-Content -Raw -LiteralPath $cfg | ConvertFrom-Json).source_clones.agentm)
        if ($clone) {
            $cand = Join-Path $clone "scripts/harness_memory.py"
            if (Test-Path -LiteralPath $cand) { $resolver = $cand }
        }
    } catch { }
}
if (-not $resolver) {
    $fallback = Join-Path $HOME "Antigravity/agentm/scripts/harness_memory.py"
    if (Test-Path -LiteralPath $fallback) { $resolver = $fallback }
}
if (-not $resolver) { Write-Skip "harness_memory.py resolver unavailable" }

function Resolve-State([string]$name) {
    # The bash twin enforces the hard 500ms budget; vault-state-path is a fast
    # local resolve, so the pwsh twin runs it directly (degraded-graceful, DC-3).
    try {
        Push-Location -LiteralPath $eventCwd
        $out = & $py $resolver vault-state-path $name 2>$null
        return ([string]$out).Trim()
    } catch {
        return ""
    } finally {
        Pop-Location -ErrorAction SilentlyContinue
    }
}

$planPath = Resolve-State "PLAN.md"
$progressPath = Resolve-State "progress.md"

if ($planPath -and $progressPath -and (Test-Path -LiteralPath $planPath) -and (Test-Path -LiteralPath $progressPath)) {
    Write-Output "[agentm] Project state for this repo lives in the vault, not in .harness/:"
    Write-Output "  PLAN.md:     $planPath"
    Write-Output "  progress.md: $progressPath"
    Write-Output "Read PLAN.md before answering plan-status questions or running /work, /review, /release."
    $slug = Split-Path (Split-Path (Split-Path $planPath -Parent) -Parent) -Leaf
    [Console]::Error.WriteLine("[harness-context] injected vault paths for slug=$slug")
} else {
    # Unconfigured project? Delegate the nudge decision to project_config.py
    # should-nudge (.git + not registered + no .agentm-no-register + not a
    # harness-source bypass). All logic in testable Python; the hook only emits. V4 #32.
    $pc = Join-Path (Split-Path $resolver -Parent) "project_config.py"
    $nudge = $false
    if (Test-Path -LiteralPath $pc) {
        try {
            Push-Location -LiteralPath $eventCwd
            & $py $pc should-nudge . 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $nudge = $true }
        } catch {
            $nudge = $false
        } finally {
            Pop-Location -ErrorAction SilentlyContinue
        }
    }
    if ($nudge) {
        Write-Output "[agentm] New project — I haven't configured this repo. Say 'configure this project' or run /setup --detect."
        [Console]::Error.WriteLine("[harness-context] configure-nudge emitted for $eventCwd")
    } else {
        [Console]::Error.WriteLine("[harness-context] non-harness cwd or vault paths unresolved — skipped")
    }
}
exit 0
