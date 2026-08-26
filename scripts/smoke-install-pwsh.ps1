# smoke-install-pwsh.ps1 — install agentm machine-wide into a scratch prefix and
# assert the expected tree, the installer boundary, idempotence, and that a
# re-run preserves the operator's own settings.json entries.
#
# Used by tests-windows.yml. Invoked from repo root:
#   pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1
#
# Semantic twin of smoke-install-bash.sh. Exits non-zero on failure.
#
# HERMETIC BY CONSTRUCTION — a scratch HOME as well as a scratch prefix.
# $env:AGENTM_INSTALL_PREFIX redirects only the customizations tree; the
# installer keys its PATH launcher (and, on the bash side, the launchd daemon
# and ~/.gemini/GEMINI.md) off $HOME. Without the fake HOME this suite would
# rewrite the developer's real launcher.
#
# The fake HOME also FORCES RELEASE MODE (no ~/Antigravity/agentm clone at it),
# which is load-bearing: a developer running this locally on a machine with a
# real clone would otherwise silently exercise source mode and mask a
# release-mode-only regression — the bug class that once landed
# harness/{agents,skills,hooks} flat under the prefix instead of nested.

$ErrorActionPreference = 'Stop'
$HarnessRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-smoke-" + [System.Guid]::NewGuid().ToString('N'))
$prefix = Join-Path $scratch 'prefix'
$fakeHome = Join-Path $scratch 'home'
New-Item -ItemType Directory -Path $prefix -Force | Out-Null
New-Item -ItemType Directory -Path $fakeHome -Force | Out-Null

$origHome = $env:HOME
$origPrefix = $env:AGENTM_INSTALL_PREFIX
$origCI = $env:CI

function Invoke-Install {
    param([string[]]$InstallArgs = @())
    $env:HOME = $fakeHome
    $env:AGENTM_INSTALL_PREFIX = $prefix
    $env:CI = 'true'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "install.ps1 exited $LASTEXITCODE" }
}

try {
    Write-Host "==> fresh install into $prefix"
    Invoke-Install | Out-File (Join-Path $scratch 'install.log')

    $fail = $false

    # ── release mode really was forced (keeps the rest of the run honest) ───
    $configPath = Join-Path $prefix '.agentm-config.json'
    if (-not (Test-Path -LiteralPath $configPath)) {
        Write-Host 'FAIL: install did not write .agentm-config.json'
        $fail = $true
    } else {
        $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
        if ($config.mode -ne 'release') {
            Write-Host "FAIL: .agentm-config.json mode is not 'release' despite the empty fake HOME: $($config.mode)"
            $fail = $true
        }
    }

    # ── expected files: the machine-wide tree ──────────────────────────────
    $expected = @(
        '.agentm-config.json'
        'settings.json'
        'agents/adapt-evaluator.md'
        'agents/memory-idea-researcher.md'
        'skills/doctor/SKILL.md'
        'skills/memory/SKILL.md'
        'skills/console/SKILL.md'
        'skills/design/SKILL.md'
        'hooks/harness-context-session-start/harness-context-session-start.ps1'
        'hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.ps1'
        'hooks/memory-recall-session-start/memory-recall-session-start.ps1'
        'hooks/memory-reflect-idle/memory-reflect-idle.ps1'
        'hooks/memory-reflect-stop/memory-reflect-stop.ps1'
        'hooks/verify-dispatch/verify-dispatch.ps1'
    )
    foreach ($p in $expected) {
        if (-not (Test-Path -LiteralPath (Join-Path $prefix $p))) {
            Write-Host "MISSING: $p"
            $fail = $true
        }
    }

    # ── installer boundary: this repo's own tooling must NOT leak ──────────
    $leaks = @(
        '.github/workflows/tests-linux.yml'
        '.github/workflows/tests-mac.yml'
        '.github/workflows/tests-windows.yml'
        'scripts/smoke-install-bash.sh'
        'scripts/smoke-install-pwsh.ps1'
        'scripts/check-parity.sh'
        'scripts/validate-adapters.py'
        'scripts/check-references.py'
        'scripts/check-integrity-bash.sh'
        'scripts/check-integrity-pwsh.ps1'
        'wiki/Home.md'
    )
    foreach ($p in $leaks) {
        if (Test-Path -LiteralPath (Join-Path $prefix $p)) {
            Write-Host "LEAK: $p should not be in the install prefix (installer boundary)"
            $fail = $true
        }
    }

    # ── the per-project install is really gone ─────────────────────────────
    # Every one of these was produced by the retired -Scope project path.
    # Finding any means a project-scope code path survived the collapse.
    $retiredProjectArtifacts = @(
        '.harness/PLAN.md'
        '.harness/features.json'
        '.harness/progress.md'
        '.harness/init.sh'
        '.harness/.version'
        '.harness/verify.ps1'
        '.harness/hooks/precompact.ps1'
        '.harness/hooks/session-start-compact.ps1'
        '.claude/settings.json'
        '.agents/rules/harness.md'
        '.gemini/settings.json'
        'AGENTS.md'
        'CLAUDE.md'
    )
    foreach ($p in $retiredProjectArtifacts) {
        foreach ($root in @($prefix, $fakeHome)) {
            if (Test-Path -LiteralPath (Join-Path $root $p)) {
                Write-Host "PROJECT-SCOPE LEAK: $root/$p — the per-project install is retired"
                $fail = $true
            }
        }
    }

    # ── V5 dev-loop slim: phase commands + review sub-agents must NOT install ─
    $slimmed = @(
        'commands/plan.md'
        'commands/work.md'
        'commands/review.md'
        'commands/release.md'
        'commands/setup.md'
        'commands/bugfix.md'
        'agents/explorer.md'
        'agents/adversarial-reviewer.md'
        'agents/adversarial-reviewer-cross.md'
        'skills/explorer/SKILL.md'
    )
    foreach ($p in $slimmed) {
        if (Test-Path -LiteralPath (Join-Path $prefix $p)) {
            Write-Host "SLIM-LEAK: $p should NOT install after the V5 dev-loop slim"
            $fail = $true
        }
    }

    # ── settings.json: valid JSON, all hook events stored as arrays ────────
    $settingsPath = Join-Path $prefix 'settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath)) {
        Write-Host 'FAIL: settings.json was never created'
        $fail = $true
    } else {
        $settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
        if (-not $settings.hooks) {
            Write-Host 'FAIL: hooks missing'
            $fail = $true
        } else {
            foreach ($evt in $settings.hooks.PSObject.Properties.Name) {
                $v = $settings.hooks.$evt
                if ($v -isnot [System.Array]) {
                    Write-Host "FAIL: hooks.$evt is not an array (got $($v.GetType().Name))"
                    $fail = $true
                } elseif ($v.Count -lt 1) {
                    Write-Host "FAIL: hooks.$evt is empty"
                    $fail = $true
                }
            }
            Write-Host "    settings.json OK ($($settings.hooks.PSObject.Properties.Name.Count) events)"
        }
    }

    # ── every installed hook's fragment actually merged ────────────────────
    # Installing hook dirs is not enough: each hook's settings-fragment-pwsh.json
    # must be merged into <prefix>/settings.json, absolutized to the installed
    # script. Dropping the dirs without merging is the bug GH #72 fixed on this
    # side, and it is silent — the hooks simply never fire. This assertion was
    # deferred when the pwsh merge did not yet exist; it does now.
    $userHooks = @(
        'harness-context-session-start'
        'memory-recall-prompt-submit'
        'memory-recall-session-start'
        'memory-reflect-idle'
        'memory-reflect-stop'
        'verify-dispatch'
    )
    if (Test-Path -LiteralPath $settingsPath) {
        $settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
        $commands = @()
        foreach ($evt in $settings.hooks.PSObject.Properties.Name) {
            foreach ($entry in $settings.hooks.$evt) {
                foreach ($h in $entry.hooks) { $commands += $h.command }
            }
        }
        $missing = @($userHooks | Where-Object { $name = $_; -not ($commands | Where-Object { $_ -like "*$name.ps1*" }) })
        if ($missing.Count -gt 0) {
            Write-Host "FAIL: settings.json has no merged fragment for: $($missing -join ', ')"
            $fail = $true
        } else {
            Write-Host "    settings.json: all $($userHooks.Count) hook fragments merged"
        }
    }

    if ($fail) {
        Write-Host 'FAIL: expected-files / boundary / settings.json assertions failed'
        exit 1
    }

    # ── post-install integrity ─────────────────────────────────────────────
    Write-Host '==> post-install integrity'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'scripts/check-integrity-pwsh.ps1') $prefix
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'FAIL: check-integrity-pwsh failed'
        exit 1
    }

    # ── a re-run is idempotent, and preserves what the operator owns ───────
    # The retired -Update flag used to carry this contract for cp_user files.
    # The machine-wide equivalent is settings.json: a refresh must merge without
    # duplicating its own entries and without dropping entries the operator
    # added by hand. Same intent, current contract.
    Write-Host '==> re-run is idempotent + preserves operator-authored settings'
    $settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
    $marker = [PSCustomObject]@{
        matcher = '.*'
        hooks   = @([PSCustomObject]@{ type = 'command'; command = 'pwsh -NoProfile -File C:/operator/authored/marker.ps1' })
    }
    if ($settings.hooks.PSObject.Properties.Name -contains 'Stop') {
        $settings.hooks.Stop = @($settings.hooks.Stop) + $marker
    } else {
        $settings.hooks | Add-Member -NotePropertyName 'Stop' -NotePropertyValue @($marker)
    }
    ($settings | ConvertTo-Json -Depth 10) | Set-Content -LiteralPath $settingsPath

    $before = 0
    foreach ($evt in $settings.hooks.PSObject.Properties.Name) {
        foreach ($entry in $settings.hooks.$evt) { $before += @($entry.hooks).Count }
    }

    Invoke-Install | Out-File (Join-Path $scratch 'rerun.log')

    $after = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
    $afterCommands = @()
    foreach ($evt in $after.hooks.PSObject.Properties.Name) {
        foreach ($entry in $after.hooks.$evt) {
            foreach ($h in $entry.hooks) { $afterCommands += $h.command }
        }
    }
    if (-not ($afterCommands | Where-Object { $_ -like '*C:/operator/authored/marker.ps1*' })) {
        Write-Host 'FAIL: re-run dropped an operator-authored settings.json hook'
        exit 1
    }
    if ($afterCommands.Count -ne $before) {
        Write-Host "FAIL: re-run changed the hook count $before -> $($afterCommands.Count) (a refresh must not duplicate its own entries)"
        exit 1
    }
    Write-Host "    re-run idempotent ($($afterCommands.Count) hook commands, operator entry intact)"

    # ── -LocalState: first-class repo-local (vault-less) mode ──────────────
    # Hardening I #44 task 4. The state-mode axis is orthogonal to install
    # scope and survives it intact.
    Write-Host '==> -LocalState writes state_mode:local + state lands repo-local'
    $localPrefix = Join-Path $scratch 'local-prefix'
    $localProject = Join-Path $scratch 'local-project'
    New-Item -ItemType Directory -Path $localPrefix -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $localProject '.harness') -Force | Out-Null

    $env:AGENTM_INSTALL_PREFIX = $localPrefix
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') -LocalState | Out-File (Join-Path $scratch 'local.log')
    if ($LASTEXITCODE -ne 0) { Write-Host 'FAIL: -LocalState install failed'; exit 1 }

    $localConfig = Get-Content -Raw -LiteralPath (Join-Path $localPrefix '.agentm-config.json') | ConvertFrom-Json
    if ($localConfig.state_mode -ne 'local') {
        Write-Host "FAIL: state_mode not 'local': $($localConfig.state_mode)"
        exit 1
    }
    Write-Host '    state_mode:local OK'

    '{"vault_project": "smokedemo"}' | Set-Content -LiteralPath (Join-Path $localProject '.harness/project.json')
    $py = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
    if ($py) {
        $prevVault = $env:MEMORY_VAULT_PATH
        Remove-Item Env:\MEMORY_VAULT_PATH -ErrorAction SilentlyContinue
        try {
            '# smoke PLAN' | & $py.Source (Join-Path $HarnessRoot 'scripts/harness_memory.py') `
                write-state --project-root $localProject 'PLAN.md' | Out-Null
            if (-not (Test-Path -LiteralPath (Join-Path $localProject '.harness/PLAN.md'))) {
                Write-Host 'FAIL: -LocalState write-state did not land repo-local at .harness/PLAN.md'
                exit 1
            }
            $got = & $py.Source (Join-Path $HarnessRoot 'scripts/harness_memory.py') `
                read-state --project-root $localProject 'PLAN.md'
            if ($got.Trim() -ne '# smoke PLAN') {
                Write-Host "FAIL: -LocalState read-state round-trip mismatch: got '$got'"
                exit 1
            }
            Write-Host '    repo-local write/read round-trip OK'
        } finally {
            if ($prevVault) { $env:MEMORY_VAULT_PATH = $prevVault }
        }
    }

    # ── retired parameters fail rather than being ignored ──────────────────
    # PowerShell rejects unknown named parameters at bind time, so this asserts
    # the parameters really were removed rather than quietly accepted.
    Write-Host '==> retired parameters are rejected'
    $env:AGENTM_INSTALL_PREFIX = $prefix
    foreach ($p in @('-Scope', '-Update', '-Hooks')) {
        & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') $p 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "FAIL: $p was accepted — retired parameters must fail, not no-op"
            exit 1
        }
    }
    Write-Host '    -Scope / -Update / -Hooks all rejected'

    Write-Host '==> smoke-install-pwsh: OK'
} finally {
    if ($origHome) { $env:HOME = $origHome } else { Remove-Item Env:\HOME -ErrorAction SilentlyContinue }
    if ($origPrefix) { $env:AGENTM_INSTALL_PREFIX = $origPrefix } else { Remove-Item Env:\AGENTM_INSTALL_PREFIX -ErrorAction SilentlyContinue }
    if ($origCI) { $env:CI = $origCI } else { Remove-Item Env:\CI -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force -LiteralPath $scratch -ErrorAction SilentlyContinue
}
