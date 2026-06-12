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

# ── Named plans (V5-10): enumerate PLAN-<name>.md alongside the singleton ──
# The _harness/ dir is the parent of the resolved (vault) PLAN.md path - pure
# path construction, so it resolves even when the unnamed PLAN.md is absent on
# disk. Glob PLAN-*.md there, skipping GDrive conflict copies. Back-compat: a
# vault holding only the unnamed PLAN.md finds zero named plans and falls
# through to the LOCKED singleton block below, byte-identical.
$harnessDir = if ($planPath) { Split-Path $planPath -Parent } else { "" }
$namedPlans = @()
if ($harnessDir -and (Test-Path -LiteralPath $harnessDir -PathType Container)) {
    $namedPlans = @(
        Get-ChildItem -LiteralPath $harnessDir -Filter 'PLAN-*.md' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '*(conflicted copy*' } |
            Sort-Object Name
    )
}

# ── Inject: named-plan mode → singleton (DC-7, locked) → nudge/skip ──
if ($namedPlans.Count -gt 0) {
    # Named-plan mode: surface every PLAN*.md + the .harness/active-plan binding.
    Write-Output "[agentm] Project state for this repo lives in the vault, not in .harness/:"
    Write-Output "Named-plan mode - this repo has more than one active plan:"
    if ($planPath -and (Test-Path -LiteralPath $planPath)) {
        Write-Output ("  {0,-22} {1}" -f "PLAN.md", $planPath)
    }
    foreach ($pf in $namedPlans) {
        Write-Output ("  {0,-22} {1}" -f $pf.Name, $pf.FullName)
    }
    # Active-plan binding - the worktree-local .harness/active-plan marker (read
    # here, written by component (2)'s worktree-spawn helper). A present marker
    # naming an absent PLAN-<name>.md is dangling - surfaced, not fatal.
    $marker = Join-Path (Join-Path $eventCwd ".harness") "active-plan"
    if (Test-Path -LiteralPath $marker) {
        $bind = ([string](Get-Content -LiteralPath $marker -TotalCount 1 -ErrorAction SilentlyContinue)) -replace '\s', ''
        if ($bind) {
            $boundPlan = Join-Path $harnessDir "PLAN-$bind.md"
            if (Test-Path -LiteralPath $boundPlan) {
                Write-Output "Active plan (.harness/active-plan -> $bind): $boundPlan"
            } else {
                Write-Output "Active plan (.harness/active-plan -> $bind): DANGLING - PLAN-$bind.md not found; run doctor."
            }
        }
    }
    Write-Output "Read the plan you own (or the .harness/active-plan one) before /work, /review, /release."
    $slug = Split-Path (Split-Path $harnessDir -Parent) -Leaf
    [Console]::Error.WriteLine("[harness-context] injected $($namedPlans.Count) named plan(s) for slug=$slug")
} elseif ($planPath -and $progressPath -and (Test-Path -LiteralPath $planPath) -and (Test-Path -LiteralPath $progressPath)) {
    # Singleton path - LOCKED DC-7 4-line block, byte-identical (back-compat).
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
