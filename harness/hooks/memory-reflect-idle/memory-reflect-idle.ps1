# memory-reflect-idle — orphan-recovery + idle reflection sweep (Windows / pwsh).
# Mirrors memory-reflect-idle.sh.
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern.

$ReflectPy = ".claude/skills/memory/scripts/reflect.py"
if (-not (Test-Path $ReflectPy)) {
    exit 0
}

if (-not (Get-Command python3 -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue)) {
    exit 0
}

$Py = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }

# Thresholds (env overrides + defaults).
$IdleThresholdSec = if ($env:MEMORY_IDLE_THRESHOLD_SEC) { [int]$env:MEMORY_IDLE_THRESHOLD_SEC } else { 3600 }
$GcThresholdSec = if ($env:MEMORY_REFLECTED_GC_SEC) { [int]$env:MEMORY_REFLECTED_GC_SEC } else { 2592000 }

$HarnessDir = ".harness"
if (-not (Test-Path $HarnessDir)) {
    exit 0
}

$markers = @(Get-ChildItem -LiteralPath $HarnessDir -Filter 'session-id-*.start' -ErrorAction SilentlyContinue)
$reflectedMarkers = @(Get-ChildItem -LiteralPath $HarnessDir -Filter 'session-id-*.reflected' -ErrorAction SilentlyContinue)

# Note: removed an early `exit 0` here so the skill-discovery cadence-check
# (plan #7b task 3) still runs even when there's no orphan work to do.

$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$processedCount = 0

foreach ($marker in $markers) {
    $mtime = [DateTimeOffset]::new($marker.LastWriteTimeUtc).ToUnixTimeSeconds()
    $ageSec = $now - $mtime
    if ($ageSec -lt $IdleThresholdSec) {
        continue
    }

    # Parse transcript path from marker contents.
    $markerContents = Get-Content -LiteralPath $marker.FullName -ErrorAction SilentlyContinue
    $transcript = ($markerContents | Where-Object { $_ -match '^transcript:' } | Select-Object -First 1) -replace '^transcript:\s*', ''
    if (-not $transcript) {
        [Console]::Error.WriteLine("[memory-reflect-idle] marker $($marker.Name) missing 'transcript:' line (skipping)")
        continue
    }
    if (-not (Test-Path -LiteralPath $transcript)) {
        [Console]::Error.WriteLine("[memory-reflect-idle] marker $($marker.Name) transcript not found: $transcript (skipping)")
        continue
    }

    # Run reflection with --route (HIGH → canonical / MEDIUM+LOW → _inbox/).
    & $Py $ReflectPy $transcript "--summary" "--route" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $reflectedPath = $marker.FullName -replace '\.start$', '.reflected'
        try {
            Move-Item -LiteralPath $marker.FullName -Destination $reflectedPath -Force -ErrorAction Stop
            $processedCount++
        } catch {
            # Rename failed; marker stays for next pass.
        }
    }
}

# GC pass.
$gcCount = 0
foreach ($reflected in $reflectedMarkers) {
    $mtime = [DateTimeOffset]::new($reflected.LastWriteTimeUtc).ToUnixTimeSeconds()
    $ageSec = $now - $mtime
    if ($ageSec -gt $GcThresholdSec) {
        try {
            Remove-Item -LiteralPath $reflected.FullName -Force -ErrorAction Stop
            $gcCount++
        } catch {}
    }
}

if ($markers.Count -gt 0 -or $gcCount -gt 0) {
    [Console]::Error.WriteLine("[memory-reflect-idle] Scanned $($markers.Count) .start + $($reflectedMarkers.Count) .reflected markers; processed $processedCount orphans, GC'd $gcCount old markers (idle threshold: ${IdleThresholdSec}s)")
}

# ── Idle orchestration chain (V4 #23 task 4) ──────────────────────────────
# Fire the cooldown-gated chain driver: reflect-corpus (≤5 unseen sessions) →
# discover-skills (cadence-checked) → adapt_skills Pass-1 (≤3 candidates
# staged). The driver self-gates on the idle_chain cooldown (default 24h) +
# the enable_idle_chain toggle, so most invocations are a fast no-op; when it
# DOES fire it can exceed this hook's 30s SessionStart timeout, so we launch
# it DETACHED (hidden, no -Wait) and return immediately. Results surface on the
# NEXT session via the task-3 briefing. Graceful-skip if MEMORY_VAULT_PATH
# unset / driver absent.
$OrchIdlePy = ".claude/skills/memory/scripts/orchestration_idle.py"
$VaultEnv = $env:MEMORY_VAULT_PATH
if ((Test-Path $OrchIdlePy) -and $VaultEnv) {
    try {
        Start-Process -FilePath $Py -ArgumentList @($OrchIdlePy, "--vault-path", $VaultEnv) -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Non-fatal — the hook never blocks on the idle-chain launch.
    }
}

# ── Vec-index drift sweep (V4 #37 task 6) ─────────────────────────────────
# Fire vec_index.py full-sync (read-only; no --rebuild) so drift
# accumulation surfaces non-blockingly. Graceful-skip if MEMORY_VAULT_PATH
# unset / vec_index.py absent / sqlite-vec unavailable. Operators with
# drift accumulation run `vec_index.py full-sync --rebuild` to enqueue.
$VecIndexPy = ".claude/skills/memory/scripts/vec_index.py"
if ((Test-Path $VecIndexPy) -and $VaultEnv) {
    try {
        $driftJson = & $Py $VecIndexPy "--vault-path" $VaultEnv "full-sync" 2>$null
        if ($driftJson) {
            $parsed = $driftJson | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($parsed -and (($parsed.drifted_count -gt 0) -or ($parsed.not_indexed_count -gt 0))) {
                [Console]::Error.WriteLine("[memory-reflect-idle] vec-index drift sweep: $($parsed.drifted_count) drifted + $($parsed.not_indexed_count) not-indexed (run ``pwsh -File $VecIndexPy full-sync --rebuild`` to enqueue for re-embed)")
            }
        }
    } catch {
        # Non-fatal — the hook never blocks on drift-sweep failure.
    }
}

exit 0
