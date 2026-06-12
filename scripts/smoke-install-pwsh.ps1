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
        '.claude/skills/doctor/SKILL.md',
        '.claude/settings.json',
        '.agents/rules/harness.md',
        '.agents/workflows/plan.md',
        '.agents/skills/documenter/SKILL.md',
        '.agents/skills/doctor/SKILL.md',
        '.gemini/commands/plan.toml',
        '.gemini/agents/explorer.md',
        '.gemini/settings.json',
        'wiki/Home.md',
        'wiki/README.md',
        'wiki/_Sidebar.md',
        'wiki/.diataxis',
        'wiki/how-to/01-Getting-Started.md',
        'wiki/how-to/First-How-To.md',
        'wiki/reference/First-Reference.md',
        'wiki/explanation/First-Explanation.md',
        'wiki/decisions/README.md',
        'wiki/designs/README.md',
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
        'scripts/validate-adapters.py',
        'scripts/check-references.py',
        'scripts/check-syntax.sh',
        'scripts/check-syntax.ps1',
        'scripts/check-integrity-bash.sh',
        'scripts/check-integrity-pwsh.ps1'
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

    # -Update preserves user edits (cp_user semantics)
    Write-Host '==> -Update preserves user edits (cp_user semantics)'
    $userMark = "# USER-EDIT-MARKER-$([Guid]::NewGuid().ToString('N'))"
    $userMark2 = "# USER-AGENTS-MARKER-$([Guid]::NewGuid().ToString('N'))"
    Add-Content -LiteralPath (Join-Path $scratch 'wiki/Home.md') -Value $userMark
    Add-Content -LiteralPath (Join-Path $scratch 'AGENTS.md') -Value $userMark2

    # -Update refresh
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -Update -Hooks $scratch | Out-File (Join-Path $scratch '.update.log')
    $updateLog = Get-Content -LiteralPath (Join-Path $scratch '.update.log') -Raw

    $homeContent = Get-Content -LiteralPath (Join-Path $scratch 'wiki/Home.md') -Raw
    if ($homeContent -notmatch [regex]::Escape($userMark)) {
        Write-Host 'FAIL: -Update clobbered user edit in wiki/Home.md'
        exit 1
    }
    $agentsContent = Get-Content -LiteralPath (Join-Path $scratch 'AGENTS.md') -Raw
    if ($agentsContent -notmatch [regex]::Escape($userMark2)) {
        Write-Host 'FAIL: -Update clobbered user edit in AGENTS.md'
        exit 1
    }

    if (-not ($updateLog -match '(up to date|updated)')) {
        Write-Host 'FAIL: -Update produced no up-to-date/updated markers'
        exit 1
    }

    # Post-install integrity check
    Write-Host '==> post-install integrity'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'scripts/check-integrity-pwsh.ps1') $scratch
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'FAIL: check-integrity-pwsh failed'
        exit 1
    }

    Write-Host '==> smoke-install-pwsh: OK'
}
finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
