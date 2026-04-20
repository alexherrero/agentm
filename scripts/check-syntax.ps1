# check-syntax.ps1 — parse every .ps1 under the repo via PowerShell AST.
# Catches syntax errors in dead branches, mismatched braces, etc. that
# only a full parse exposes.
#
# Scope:
#   - install.ps1 (repo root)
#   - scripts/*.ps1
#   - templates/**/*.ps1
#   - adapters/**/*.ps1  (none today, but future-proof)
#
# Exits non-zero on first parse failure. Prints count on success.

$ErrorActionPreference = 'Stop'

$HarnessRoot = Split-Path -Parent $PSScriptRoot
Set-Location $HarnessRoot

$targets = @()
$targets += Get-ChildItem -LiteralPath $HarnessRoot -Filter 'install.ps1' -File -ErrorAction SilentlyContinue
foreach ($dir in @('scripts', 'templates', 'adapters')) {
    $p = Join-Path $HarnessRoot $dir
    if (Test-Path -LiteralPath $p) {
        $targets += Get-ChildItem -LiteralPath $p -Filter '*.ps1' -File -Recurse -ErrorAction SilentlyContinue
    }
}

$fail = $false
$count = 0
foreach ($t in $targets) {
    $parseErrors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $t.FullName, [ref]$tokens, [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        Write-Host "FAIL: parse errors in $($t.FullName):"
        foreach ($e in $parseErrors) {
            Write-Host "  $($e.Message) at line $($e.Extent.StartLineNumber)"
        }
        $fail = $true
    }
    $count++
}

if ($fail) {
    exit 1
}

Write-Host "check-syntax: $count .ps1 files parse cleanly."
