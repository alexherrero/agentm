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
        '.claude/commands/recent-wiki-changes.md',
        '.claude/agents/adapt-evaluator.md',
        '.claude/skills/doctor/SKILL.md',
        '.claude/settings.json',
        '.agents/rules/harness.md',
        '.agents/skills/doctor/SKILL.md',
        '.gemini/settings.json',
        'wiki/Home.md',
        'wiki/README.md',
        'wiki/_Sidebar.md',
        'wiki/.diataxis',
        'wiki/how-to/01-Getting-Started.md',
        'wiki/how-to/First-How-To.md',
        'wiki/reference/First-Reference.md',
        'wiki/explanation/First-Explanation.md',
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

    # V5 dev-loop slim: the phase commands + review sub-agents must NOT install.
    # They moved to the crickets developer-workflows / code-review plugins.
    $slimmed = @(
        '.claude/commands/plan.md',
        '.claude/commands/work.md',
        '.claude/commands/review.md',
        '.claude/commands/release.md',
        '.claude/commands/setup.md',
        '.claude/commands/bugfix.md',
        '.claude/agents/explorer.md',
        '.claude/agents/adversarial-reviewer.md',
        '.claude/agents/adversarial-reviewer-cross.md',
        '.claude/hooks/evidence-tracker.ps1',
        '.agents/workflows/plan.md',
        '.agents/skills/explorer/SKILL.md',
        '.gemini/commands/plan.toml',
        '.gemini/agents/explorer.md'
    )
    foreach ($p in $slimmed) {
        $full = Join-Path $scratch $p
        if (Test-Path -LiteralPath $full) {
            Write-Host "SLIM-LEAK: $p should NOT install after the V5 dev-loop slim" -ErrorAction Continue
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

    # -LocalState: first-class repo-local (vault-less) mode (Hardening I #44
    # task 4). Mirrors smoke-install-bash.sh's equivalent section — proves
    # the entry point end-to-end on Windows too: the flag writes
    # state_mode:local to .agentm-config.json (DC-8), and a subsequent
    # state write lands repo-local with no vault configured.
    Write-Host '==> -LocalState writes state_mode:local + state lands repo-local'
    $localScratch = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-smoke-local-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $localScratch -Force | Out-Null
    try {
        & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -LocalState $localScratch | Out-File (Join-Path $localScratch '.install.log')

        $configPath = Join-Path $localScratch '.claude/.agentm-config.json'
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($config.state_mode -ne 'local') {
            Write-Host "FAIL: state_mode not 'local': $($config.state_mode)"
            exit 1
        }
        Write-Host '    state_mode:local OK'

        $projectJsonPath = Join-Path $localScratch '.harness/project.json'
        New-Item -ItemType Directory -Path (Join-Path $localScratch '.harness') -Force | Out-Null
        Set-Content -LiteralPath $projectJsonPath -Value '{"vault_project": "smokedemo"}'

        $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
        if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
        $env:AGENTM_INSTALL_PREFIX = (Join-Path $localScratch '.claude')
        $env:MEMORY_VAULT_PATH = $null
        "# smoke PLAN" | & $pythonCmd.Source (Join-Path $HarnessRoot 'scripts/harness_memory.py') `
            write-state --project-root $localScratch 'PLAN.md' | Out-Null
        $env:AGENTM_INSTALL_PREFIX = $null

        $planPath = Join-Path $localScratch '.harness/PLAN.md'
        if (-not (Test-Path -LiteralPath $planPath)) {
            Write-Host 'FAIL: -LocalState write-state did not land repo-local at .harness/PLAN.md'
            exit 1
        }
        $got = (Get-Content -LiteralPath $planPath -Raw).Trim()
        if ($got -ne '# smoke PLAN') {
            Write-Host "FAIL: -LocalState read-state round-trip mismatch: got '$got'"
            exit 1
        }
        Write-Host '    repo-local write/read round-trip OK'
    }
    finally {
        if (Test-Path -LiteralPath $localScratch) {
            Remove-Item -LiteralPath $localScratch -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # -Scope user: hook dirs land + install state persists (Loose Ends
    # Release 3 Plan A, GH #70). Structural-only on purpose: install.ps1's
    # -Scope user path has no equivalent of install.sh's
    # _agentm_merge_user_hook_fragments (the V4 #39 fix) yet, so
    # .claude/settings.json is never even created under -Scope user today —
    # not just unmerged, genuinely absent. That's the real bug GH #72 (Plan
    # B) fixes; this task only proves what already works: the hook
    # directories land, and install state persists to .agentm-config.json.
    # Plan B adds the settings.json assertion here once the fix lands,
    # mirroring smoke-install-bash.sh's task-1 coverage.
    #
    # Deliberately forces release mode (a scratch HOME with no agentm clone
    # at it) rather than relying on "no CI runner has a clone" — a developer
    # running this locally from a machine with a real Antigravity/agentm
    # clone would otherwise silently exercise source mode instead, masking
    # a release-mode-only regression (harness/{agents,skills,hooks} landing
    # flat under the prefix instead of nested is exactly the class of bug
    # this task-1's bash twin found and fixed the same way).
    Write-Host '==> -Scope user: hook dirs land + install state persists'
    $userScratch = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-smoke-user-" + [System.Guid]::NewGuid().ToString('N'))
    $fakeHome = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-smoke-fakehome-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $fakeHome -Force | Out-Null
    $env:AGENTM_INSTALL_PREFIX = $userScratch
    $env:HOME = $fakeHome
    try {
        & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -Scope user | Out-File (Join-Path $scratch '.user-install.log')

        $userHooks = @(
            'harness-context-session-start',
            'memory-recall-prompt-submit',
            'memory-recall-session-start',
            'memory-reflect-idle',
            'memory-reflect-stop'
        )
        foreach ($h in $userHooks) {
            $hookScript = Join-Path $userScratch "hooks/$h/$h.sh"
            if (-not (Test-Path -LiteralPath $hookScript)) {
                Write-Host "FAIL: -Scope user did not install hooks/$h/$h.sh"
                exit 1
            }
        }

        $configPath = Join-Path $userScratch '.agentm-config.json'
        if (-not (Test-Path -LiteralPath $configPath)) {
            Write-Host 'FAIL: -Scope user did not write .agentm-config.json'
            exit 1
        }
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($config.mode -ne 'release') {
            Write-Host "FAIL: .agentm-config.json mode is not 'release' despite the forced-empty HOME: $($config.mode)"
            exit 1
        }
        Write-Host "    hook dirs + .agentm-config.json (mode: $($config.mode)) OK"
    }
    finally {
        $env:AGENTM_INSTALL_PREFIX = $null
        $env:HOME = $null
        if (Test-Path -LiteralPath $userScratch) {
            Remove-Item -LiteralPath $userScratch -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $fakeHome) {
            Remove-Item -LiteralPath $fakeHome -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host '==> smoke-install-pwsh: OK'
}
finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
