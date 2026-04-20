# smoke-install-pwsh.ps1 — install the harness into a scratch dir via the
# PowerShell installer and assert the expected file tree. Used by
# tests-windows.yml. Invoked from repo root:
#   pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1
#
# Exits non-zero on first failed assertion.

$ErrorActionPreference = 'Stop'

$HarnessRoot = Split-Path -Parent $PSScriptRoot
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-smoke-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

try {
    Write-Host "==> fresh install into $scratch"
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -Hooks $scratch | Out-File (Join-Path $scratch '.install.log')

    $expected = @(
        '.harness/PLAN.md',
        '.harness/features.json',
        '.harness/progress.md',
        '.harness/init.sh',
        '.harness/known-migrations.md',
        '.harness/.version',
        '.harness/scripts/cross-review.sh',
        '.harness/scripts/cross-review.ps1',
        '.harness/scripts/telemetry.sh',
        '.harness/verify.sh',
        '.harness/verify.ps1',
        '.harness/hooks/precompact.sh',
        '.harness/hooks/precompact.ps1',
        '.harness/hooks/session-start-compact.sh',
        '.harness/hooks/session-start-compact.ps1',
        '.claude/commands/plan.md',
        '.claude/commands/work.md',
        '.claude/agents/explorer.md',
        '.claude/agents/documenter.md',
        '.claude/skills/dependabot-fixer/SKILL.md',
        '.claude/settings.json',
        '.agent/rules/harness.md',
        '.agent/workflows/plan.md',
        '.agent/skills/dependabot-fixer/SKILL.md',
        '.agents/skills/harness-plan/SKILL.md',
        '.agents/skills/dependabot-fixer/SKILL.md',
        '.codex/agents/explorer.toml',
        '.codex/agents/documenter.toml',
        '.gemini/commands/plan.toml',
        '.gemini/agents/explorer.md',
        '.gemini/settings.json',
        'wiki/Home.md',
        'wiki/README.md',
        'wiki/_Sidebar.md',
        'wiki/design/Product-Intent.md',
        'wiki/architecture/Overview.md',
        'AGENTS.md',
        'CLAUDE.md',
        '.github/workflows/wiki-sync.yml'
    )

    $fail = $false
    foreach ($p in $expected) {
        $full = Join-Path $scratch $p
        if (-not (Test-Path -LiteralPath $full)) {
            Write-Host "MISSING: $p" -ErrorAction Continue
            $fail = $true
        }
    }

    # Installer boundary: tests-*.yml + scripts/ must NOT propagate
    $leaks = @(
        '.github/workflows/tests-linux.yml',
        '.github/workflows/tests-mac.yml',
        '.github/workflows/tests-windows.yml',
        'scripts/smoke-install-bash.sh',
        'scripts/smoke-install-pwsh.ps1',
        'scripts/check-parity.sh',
        'scripts/validate-adapters.py'
    )
    foreach ($p in $leaks) {
        $full = Join-Path $scratch $p
        if (Test-Path -LiteralPath $full) {
            Write-Host "LEAK: $p should not be in scratch install (installer boundary)" -ErrorAction Continue
            $fail = $true
        }
    }

    # settings.json: valid JSON, hook events stored as arrays
    $settingsPath = Join-Path $scratch '.claude/settings.json'
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json -AsHashtable
    if (-not $settings.hooks) { Write-Host 'FAIL: hooks missing'; $fail = $true }
    foreach ($evt in $settings.hooks.Keys) {
        $v = $settings.hooks[$evt]
        $isArray = ($v -is [System.Collections.IList]) -and ($v -isnot [string])
        if (-not $isArray) {
            Write-Host "FAIL: hooks.$evt is not an array (got $($v.GetType().Name))"
            $fail = $true
        } elseif (@($v).Count -lt 1) {
            Write-Host "FAIL: hooks.$evt is empty"
            $fail = $true
        }
    }
    Write-Host "    settings.json OK ($($settings.hooks.Keys.Count) events)"

    if ($fail) {
        Write-Host 'FAIL: assertions failed'
        exit 1
    }

    # Idempotent re-run
    Write-Host '==> idempotent re-run'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -Hooks $scratch | Out-File (Join-Path $scratch '.rerun.log')
    $rerun = Get-Content -LiteralPath (Join-Path $scratch '.rerun.log') -Raw
    if ($rerun -match 'created \.claude/settings\.json with harness hooks') {
        Write-Host 'FAIL: re-run recreated settings.json'
        exit 1
    }

    # -Update refresh
    Write-Host '==> -Update refresh'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -Update -Hooks $scratch | Out-File (Join-Path $scratch '.update.log')
    $updateLog = Get-Content -LiteralPath (Join-Path $scratch '.update.log') -Raw
    if (-not ($updateLog -match '(up to date|updated)')) {
        Write-Host 'FAIL: -Update produced no up-to-date/updated markers'
        exit 1
    }

    Write-Host '==> smoke-install-pwsh: OK'
}
finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
