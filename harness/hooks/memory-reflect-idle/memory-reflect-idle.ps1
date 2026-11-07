# memory-reflect-idle — retire the old session markers and fire the idle chain
# (Windows / pwsh). Mirrors memory-reflect-idle.sh: mining moved to the night
# (lesson_compile.py, task 199), so the orphan sweep is gone and any
# .harness/session-id-* marker left behind is deleted.
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern.

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

# ── Retired markers (task 199) ─────────────────────────────────────────────
# Nothing writes or reads a session marker any more; a marker was a pointer to a
# transcript, so deleting one loses nothing.
if (Test-Path ".harness") {
    $old = @(Get-ChildItem -LiteralPath ".harness" -Filter 'session-id-*' -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -match '\.(start|reflected)$' })
    foreach ($m in $old) {
        try { Remove-Item -LiteralPath $m.FullName -Force -ErrorAction Stop } catch {}
    }
    if ($old.Count -gt 0) {
        [Console]::Error.WriteLine("[memory-reflect-idle] cleared $($old.Count) retired session markers")
    }
}

# ── Idle orchestration chain (V4 #23 task 4) ──────────────────────────────
# Fire the cooldown-gated chain driver: discover-skills (cadence-checked) →
# adapt_skills Pass-1 (≤3 candidates staged). The driver self-gates on the idle_chain cooldown (default 24h) +
# the enable_idle_chain toggle, so most invocations are a fast no-op; when it
# DOES fire it can exceed this hook's 30s SessionStart timeout, so we launch
# it DETACHED (hidden, no -Wait) and return immediately. Results surface on the
# NEXT session via the task-3 briefing. Graceful-skip if MEMORY_ROOT (or its
# deprecated alias MEMORY_VAULT_PATH)
# unset / driver absent.
$OrchIdlePy = ".claude/skills/memory/scripts/orchestration_idle.py"
$VaultEnv = if ($env:MEMORY_ROOT) { $env:MEMORY_ROOT } else { $env:MEMORY_VAULT_PATH }
if ((Test-Path $OrchIdlePy) -and $VaultEnv) {
    try {
        Start-Process -FilePath $Py -ArgumentList @($OrchIdlePy, "--vault-path", $VaultEnv) -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Non-fatal — the hook never blocks on the idle-chain launch.
    }
}


exit 0
