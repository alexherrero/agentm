#!/usr/bin/env pwsh
# harness-context-session-start (pwsh twin) — inject the project's
# PLAN.md/progress.md paths into session context on SessionStart. State is
# backend-aware: <vault>/projects/<slug>/_harness/ when a synced backend is
# active, else device-local <project_root>/.harness/ (ADR 0020, amends ADR 0018
# DC-1). Mirrors harness-context-session-start.sh. Never blocks boot. V4 #39.

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

# ── V5-5: route plan discovery through the bridge (harness_memory.py list-plans) ──
# Output: plan paths (one per line) + "active-binding=<slug>" when binding set.
# Routes through harness_state_dir for V5-6 state_mode compat.
$plansOut = ""
try {
    Push-Location -LiteralPath $eventCwd
    $plansOut = & $py $resolver list-plans --project-root $eventCwd 2>$null
    $plansOut = ([string]$plansOut).Trim()
} catch { $plansOut = "" } finally { Pop-Location -ErrorAction SilentlyContinue }

$namedPlans = @()
$planPath = ""
$activeBinding = ""
foreach ($line in ($plansOut -split "`n")) {
    $line = $line.Trim()
    if (-not $line) { continue }
    if ($line -like 'active-binding=*') {
        $activeBinding = $line.Substring('active-binding='.Length)
    } elseif ($line -like '*PLAN.md') {
        $planPath = $line
    } elseif ($line -like '*.md') {
        $namedPlans += $line
    }
}
# progress.md is the sibling of the resolved PLAN.md: co-locate it with whatever
# _harness/ the bridge resolved $planPath into (vault when a synced backend is
# active, else device-local), so the singleton injection fires on a synced backend
# too (ADR 0020 — amends the V5-3 device-local hardcode). Fall back to the
# device-local path only when no singleton plan was resolved.
if ($planPath) {
    $progressPath = Join-Path (Split-Path -Parent $planPath) "progress.md"
} else {
    $progressPath = Join-Path (Join-Path $eventCwd ".harness") "progress.md"
}

# ── Inject: named-plan mode → singleton (DC-7, locked) → nudge/skip ──
if ($namedPlans.Count -gt 0) {
    # Named-plan mode: surface every PLAN*.md + the .harness/active-plan binding.
    Write-Output "[agentm] Project state for this repo lives in .harness/:"
    Write-Output "Named-plan mode - this repo has more than one active plan:"
    if ($planPath) {
        Write-Output ("  {0,-22} {1}" -f "PLAN.md", $planPath)
    }
    foreach ($pf in $namedPlans) {
        $pfName = Split-Path $pf -Leaf
        Write-Output ("  {0,-22} {1}" -f $pfName, $pf)
    }
    # Active-plan binding — resolved by list-plans from .harness/active-plan.
    if ($activeBinding) {
        $boundPath = $namedPlans | Where-Object { (Split-Path $_ -Leaf) -eq "PLAN-$activeBinding.md" } | Select-Object -First 1
        if ($boundPath) {
            Write-Output "Active plan (.harness/active-plan -> $activeBinding): $boundPath"
        } else {
            Write-Output "Active plan (.harness/active-plan -> $activeBinding): DANGLING - PLAN-$activeBinding.md not found; run doctor."
        }
    }
    Write-Output "Read the plan you own (or the .harness/active-plan one) before /work, /review, /release."
    $slug = Split-Path $eventCwd -Leaf
    [Console]::Error.WriteLine("[harness-context] injected $($namedPlans.Count) named plan(s) for slug=$slug")
} elseif ($planPath -and $progressPath -and (Test-Path -LiteralPath $planPath) -and (Test-Path -LiteralPath $progressPath)) {
    # Singleton path - LOCKED DC-7 4-line block, byte-identical (back-compat).
    Write-Output "[agentm] Project state for this repo lives in .harness/:"
    Write-Output "  PLAN.md:     $planPath"
    Write-Output "  progress.md: $progressPath"
    Write-Output "Read PLAN.md before answering plan-status questions or running /work, /review, /release."
    $slug = Split-Path $eventCwd -Leaf
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

# ── Observability session brief (autonomy Delivery → the workhorse line) ──
# One VISIBLE line: the latest digest headline + how long ago the last cycle
# ran, or the deadman "no digest in N days — ladder stalled" when the digest
# ladder has gone quiet. Emitted HERE — the same small-output, operator-visible
# surface as the "[agentm] Project state" line above — rather than appended to
# the memory-recall hook's multi-KB always-load dump, which the host collapses
# into a single unread <persisted-output> blob (2026-07-17 visibility fix). The
# script self-resolves the vault + telemetry paths, anti-fatigues itself, and is
# graceful on every edge. It lives next to the digest/park writers it reads.
$sessionBrief = Join-Path (Split-Path $resolver -Parent) "health/session_brief.py"
if (Test-Path -LiteralPath $sessionBrief) {
    try { & $py $sessionBrief 2>$null } catch { }
}
exit 0
