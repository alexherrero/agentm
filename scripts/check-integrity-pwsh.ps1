# check-integrity-pwsh.ps1 — post-install integrity check on a scratch dir.
#
# Called by smoke-install-pwsh.ps1 after the PowerShell installer runs
# into $Scratch. Verifies the installed tree is usable on a pwsh host:
# every hook command points at a file that exists, every installed .ps1
# parses cleanly, and settings.json uses pwsh-shell command strings (not
# bash).
#
# Usage: pwsh -NoProfile -File scripts/check-integrity-pwsh.ps1 <scratch-dir>

param(
    [Parameter(Mandatory = $true)][string]$Scratch
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Scratch -PathType Container)) {
    Write-Host "FAIL: scratch dir $Scratch does not exist"
    exit 1
}

$fail = $false

# ── 1. Hook command strings reference files that exist ───────────────────
Write-Host '  [integrity] hook command paths resolve'
$settingsPath = Join-Path $Scratch '.claude/settings.json'
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json -AsHashtable
$pathRegex = [regex]'(\.harness/[A-Za-z0-9_./-]+\.(?:sh|ps1))'
$missing = @()
foreach ($evt in $settings.hooks.Keys) {
    foreach ($item in $settings.hooks[$evt]) {
        foreach ($h in $item.hooks) {
            $cmd = [string]$h.command
            foreach ($m in $pathRegex.Matches($cmd)) {
                $rel = $m.Value
                $full = Join-Path $Scratch $rel
                if (-not (Test-Path -LiteralPath $full)) {
                    $missing += "${evt}: $rel"
                }
            }
        }
    }
}
if ($missing.Count -gt 0) {
    Write-Host 'FAIL: hook commands reference missing files:'
    foreach ($m in $missing) { Write-Host "  $m" }
    $fail = $true
} else {
    Write-Host '    hook paths OK'
}

# ── 2. pwsh host invariant: bash-only commands should NOT be present ─────
Write-Host '  [integrity] pwsh-host shell invariant'
$badCommands = @()
foreach ($evt in $settings.hooks.Keys) {
    foreach ($item in $settings.hooks[$evt]) {
        foreach ($h in $item.hooks) {
            $cmd = [string]$h.command
            # On a pwsh host, hook commands should start with `pwsh `.
            # A bash-style `bash .harness/...` command indicates the wrong
            # fragment was installed.
            if ($cmd.Trim().StartsWith('bash ')) {
                $badCommands += "${evt}: $($cmd.Substring(0, [Math]::Min(60, $cmd.Length)))"
            }
        }
    }
}
if ($badCommands.Count -gt 0) {
    Write-Host 'FAIL: pwsh install has bash-prefixed hook commands:'
    foreach ($b in $badCommands) { Write-Host "  $b" }
    $fail = $true
} else {
    Write-Host '    pwsh-host shell OK'
}

# ── 3. Every installed .ps1 parses via AST ────────────────────────────────
Write-Host '  [integrity] .ps1 syntax'
$ps1Files = Get-ChildItem -LiteralPath $Scratch -Filter '*.ps1' -File -Recurse -ErrorAction SilentlyContinue
$ps1Count = 0
foreach ($f in $ps1Files) {
    $parseErrors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $f.FullName, [ref]$tokens, [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        Write-Host "FAIL: parse errors in $($f.FullName):"
        foreach ($e in $parseErrors) {
            Write-Host "  $($e.Message) at line $($e.Extent.StartLineNumber)"
        }
        $fail = $true
    }
    $ps1Count++
}
Write-Host "    $ps1Count installed .ps1 files parse"

# ── 4. Required utility-command / agent / skill files non-empty ──────────
# The phase-gated dev loop (plan/work/review/release/bugfix) + the review
# sub-agents were slimmed out in the V5 unbundling (now provided by the crickets
# developer-workflows / code-review plugins), so they no longer install. The
# surviving harness-vendored surface is: the recent-wiki-changes utility
# command, the memory-engine sub-agents, the shared skill (doctor), and the
# Antigravity rules + Gemini settings.json. (The four-mode diataxis-migration
# skill retired to crickets' wiki-maintenance in the V5 docs slim.)
Write-Host '  [integrity] utility + agent + skill files non-empty'
$requiredNonEmpty = @(
    '.claude/commands/recent-wiki-changes.md',
    '.claude/agents/adapt-evaluator.md',
    '.claude/skills/doctor/SKILL.md',
    '.agents/rules/harness.md',
    '.agents/skills/doctor/SKILL.md',
    '.gemini/settings.json'
)
foreach ($p in $requiredNonEmpty) {
    $full = Join-Path $Scratch $p
    if (-not (Test-Path -LiteralPath $full) -or (Get-Item -LiteralPath $full).Length -eq 0) {
        Write-Host "FAIL: $p is missing or empty"
        $fail = $true
    }
}

# ── 5. settings.json schema (expected events + array shape) ──────────────
# Uses key-existence checks to match the bash checker's semantics (both
# sides accept empty-string matchers). Only non-empty `command` is required.
Write-Host '  [integrity] settings.json round-trip'
$schemaFail = $false
$expectedEvents = @('PostToolUse', 'PreCompact', 'SessionStart')
foreach ($evt in $expectedEvents) {
    if (-not $settings.hooks.ContainsKey($evt)) {
        Write-Host "FAIL: settings.json hooks missing event '$evt'"
        $schemaFail = $true
        continue
    }
    $v = $settings.hooks[$evt]
    $isList = ($v -is [System.Collections.IList]) -and ($v -isnot [string])
    if (-not $isList -or @($v).Count -lt 1) {
        Write-Host "FAIL: hooks.$evt is not a non-empty array"
        $schemaFail = $true
        continue
    }
    $first = $v[0]
    if (-not $first.ContainsKey('matcher') -or -not $first.ContainsKey('hooks')) {
        Write-Host "FAIL: hooks.$evt[0] missing matcher or hooks key"
        $schemaFail = $true
    } elseif (-not $first.hooks[0].ContainsKey('command') -or -not $first.hooks[0].command) {
        Write-Host "FAIL: hooks.$evt[0].hooks[0].command missing or empty"
        $schemaFail = $true
    }
}
if ($schemaFail) {
    $fail = $true
} else {
    Write-Host '    settings.json schema OK'
}

# ── 6. .gemini/settings.json valid JSON ──────────────────────────────────
Write-Host '  [integrity] .gemini/settings.json'
try {
    Get-Content -LiteralPath (Join-Path $Scratch '.gemini/settings.json') -Raw | ConvertFrom-Json | Out-Null
} catch {
    Write-Host "FAIL: .gemini/settings.json is not valid JSON: $_"
    $fail = $true
}

# ── 7. .harness/features.json + PLAN.md present + parseable ──────────────
Write-Host '  [integrity] .harness state files'
try {
    $feat = Get-Content -LiteralPath (Join-Path $Scratch '.harness/features.json') -Raw | ConvertFrom-Json -AsHashtable
    if (-not $feat.ContainsKey('features')) {
        Write-Host 'FAIL: .harness/features.json missing "features" key'
        $fail = $true
    }
} catch {
    Write-Host "FAIL: .harness/features.json invalid: $_"
    $fail = $true
}
$planPath = Join-Path $Scratch '.harness/PLAN.md'
if (-not (Test-Path -LiteralPath $planPath) -or (Get-Item -LiteralPath $planPath).Length -eq 0) {
    Write-Host 'FAIL: .harness/PLAN.md empty or missing'
    $fail = $true
}

if ($fail) {
    Write-Host 'check-integrity-pwsh: FAILED'
    exit 1
}
Write-Host 'check-integrity-pwsh: OK'
