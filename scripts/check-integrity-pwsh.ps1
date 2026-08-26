# check-integrity-pwsh.ps1 — post-install integrity check on a scratch install prefix.
#
# Called by smoke-install-pwsh.ps1 after the PowerShell installer runs into
# $Prefix. Verifies the installed tree is usable on a pwsh host: every hook
# command points at a file that exists, every installed .ps1 parses cleanly,
# and settings.json uses pwsh command strings (not bash).
#
# Usage: pwsh -NoProfile -File scripts/check-integrity-pwsh.ps1 <install-prefix>
#
# The hook-path check matters MORE under a machine-wide install than it did
# under the retired per-project one. Project-scope hooks were registered with
# paths relative to the project root, so a wrong path was usually still a path
# into a tree that existed. Machine-wide hooks are registered with absolute
# paths into the install prefix, and a hook whose command points at a file that
# is not there fails silently — it simply never fires, with nothing said.

param(
    [Parameter(Mandatory = $true)][string]$Prefix
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Prefix -PathType Container)) {
    Write-Host "FAIL: install prefix $Prefix does not exist"
    exit 1
}

$settingsPath = Join-Path $Prefix 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    Write-Host "FAIL: $settingsPath missing"
    exit 1
}

$fail = $false
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json -AsHashtable

# ── 1. Hook command strings reference files that exist ───────────────────
Write-Host '  [integrity] hook command paths resolve'
# Machine-wide hooks are absolutized to <prefix>/hooks/<name>/<name>.ps1 at
# merge time. Pull every .ps1/.sh path out of each command and require it to
# exist; a dangling one is a hook that will never fire.
$pathRegex = [regex]'([A-Za-z]:[\\/][^\s"'']+\.(?:ps1|sh)|/[^\s"'']+\.(?:ps1|sh))'
$normPrefix = ($Prefix -replace '\\', '/').TrimEnd('/')
$missing = @()
foreach ($evt in $settings.hooks.Keys) {
    foreach ($item in $settings.hooks[$evt]) {
        foreach ($h in $item.hooks) {
            $cmd = [string]$h.command
            foreach ($m in $pathRegex.Matches($cmd)) {
                $p = $m.Value
                # Only police paths this install owns. An operator's own hook
                # may legitimately point anywhere on their machine.
                if (($p -replace '\\', '/') -notlike "$normPrefix/*") { continue }
                if (-not (Test-Path -LiteralPath $p)) {
                    $missing += "${evt}: $p"
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

# ── 2. pwsh host invariant: no bash-prefixed commands in settings.json ────
Write-Host '  [integrity] pwsh-host shell invariant'
$bad = @()
foreach ($evt in $settings.hooks.Keys) {
    foreach ($item in $settings.hooks[$evt]) {
        foreach ($h in $item.hooks) {
            $cmd = ([string]$h.command).Trim()
            # On a pwsh host, hook commands invoke pwsh (or python). A leading
            # "bash " means the wrong fragment was installed.
            if ($cmd.StartsWith('bash ')) {
                $bad += "${evt}: $($cmd.Substring(0, [Math]::Min(60, $cmd.Length)))"
            }
        }
    }
}
if ($bad.Count -gt 0) {
    Write-Host 'FAIL: pwsh install has bash-prefixed hook commands:'
    foreach ($b in $bad) { Write-Host "  $b" }
    $fail = $true
} else {
    Write-Host '    pwsh-host shell OK'
}

# ── 3. Every installed .ps1 parses (and enough of them exist) ─────────────
Write-Host '  [integrity] .ps1 syntax'
$ps1Count = 0
foreach ($f in Get-ChildItem -LiteralPath $Prefix -Recurse -Filter '*.ps1' -File) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors -and $errors.Count -gt 0) {
        Write-Host "FAIL: parse errors in $($f.FullName)"
        foreach ($e in $errors) { Write-Host "  $($e.Message)" }
        $fail = $true
    }
    $ps1Count++
}
# The five memory/harness hooks each ship a .ps1. A count near zero means the
# installer silently skipped the PowerShell surface.
if ($ps1Count -lt 5) {
    Write-Host "FAIL: only $ps1Count .ps1 files installed — pwsh helpers missing"
    $fail = $true
}
Write-Host "    $ps1Count installed .ps1 files parse"

# ── 4. Required agent / skill files non-empty ────────────────────────────
# The phase-gated dev loop + review sub-agents were slimmed out in the V5
# unbundling (now the crickets development-lifecycle / code-review plugins).
# The surviving harness-vendored surface is the memory-engine sub-agents plus
# the shared skills.
$requiredNonEmpty = @(
    'agents/adapt-evaluator.md'
    'agents/memory-idea-researcher.md'
    'skills/doctor/SKILL.md'
    'skills/memory/SKILL.md'
)
foreach ($p in $requiredNonEmpty) {
    $full = Join-Path $Prefix $p
    if (-not (Test-Path -LiteralPath $full) -or (Get-Item -LiteralPath $full).Length -eq 0) {
        Write-Host "FAIL: $p is missing or empty"
        $fail = $true
    }
}

# ── 5. settings.json round-trips with the expected hook schema ────────────
Write-Host '  [integrity] settings.json round-trip'
if (-not $settings.hooks -or $settings.hooks.Keys.Count -eq 0) {
    Write-Host 'FAIL: settings.json registers no hooks at all'
    $fail = $true
} else {
    # Assert the SHAPE of every registered event rather than a hardcoded event
    # list: which events the shipped hooks use is theirs to change, but each
    # entry must always be a non-empty array whose first item carries a matcher
    # and at least one command, or the host silently ignores it.
    foreach ($evt in $settings.hooks.Keys) {
        $v = $settings.hooks[$evt]
        if ($v -isnot [System.Collections.IEnumerable] -or @($v).Count -lt 1) {
            Write-Host "FAIL: hooks.$evt is not a non-empty array"
            $fail = $true
            continue
        }
        $first = @($v)[0]
        if (-not $first.ContainsKey('matcher') -or -not $first.hooks) {
            Write-Host "FAIL: hooks.$evt[0] missing matcher or hooks"
            $fail = $true
            continue
        }
        if (-not @($first.hooks)[0].command) {
            Write-Host "FAIL: hooks.$evt[0].hooks[0].command is empty"
            $fail = $true
        }
    }
    Write-Host "    settings.json schema OK ($($settings.hooks.Keys.Count) events)"
}

# ── 6. install state is present and parseable ────────────────────────────
Write-Host '  [integrity] install state'
$configPath = Join-Path $Prefix '.agentm-config.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    Write-Host "FAIL: $configPath missing"
    $fail = $true
} else {
    $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    if (-not $config.harness_version) { Write-Host 'FAIL: harness_version missing'; $fail = $true }
    if (-not $config.installer_source) { Write-Host 'FAIL: installer_source missing'; $fail = $true }
}

if ($fail) {
    Write-Host 'check-integrity-pwsh: FAILED'
    exit 1
}
Write-Host 'check-integrity-pwsh: OK'
