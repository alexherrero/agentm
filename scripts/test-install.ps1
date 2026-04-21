# test-install.ps1 — dedicated installer-boundary test (pwsh twin of
# scripts/test-install.sh).
#
# Asserts the invariant that install.ps1 copies ONLY from
# $HarnessRoot/templates/ and $HarnessRoot/adapters/ — never from
# $HarnessRoot/wiki/ (this repo's own dogfood docs) or the source-repo
# mirror of a template (e.g. $HarnessRoot/.github/workflows/wiki-sync.yml,
# which is byte-identical to its template by design and therefore
# invisible to hash-based leak checks).
#
# Usage (from repo root):
#   pwsh -NoProfile -File scripts/test-install.ps1
#
# Checks:
#   (a) install.ps1 runs cleanly into a scratch dir.
#   (b) scratch/wiki/ matches templates/wiki/ byte-for-byte.
#   (c) No content from $HarnessRoot/wiki/ appears in scratch/wiki/
#       (hash-based; catches renames and content leaks the tree-diff misses).
#   (d) .github/workflows/wiki-sync.yml is present in the scratch install.
#   (e) The Ensure-BoundarySrc runtime guard fires when install.ps1 is
#       mutated to copy a managed file from outside templates/ or adapters/.
#       (Defect 2 from the wiki-sync bugfix #1 adversarial review.)
#
# Exit codes:
#   0 = installer boundary intact.
#   non-zero = boundary breached or install.ps1 is broken.

$ErrorActionPreference = 'Stop'

$HarnessRoot = Split-Path -Parent $PSScriptRoot
$Scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-testinstall-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null

$Mutated = Join-Path $HarnessRoot ("install.mutated." + [System.Guid]::NewGuid().ToString('N') + ".ps1")

function Remove-PathQuiet([string]$p) {
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
    }
}

try {
    # ── (a) install.ps1 into scratch ────────────────────────────────────────
    Write-Host "==> install into $Scratch"
    $installLog = Join-Path $Scratch '.install.log'
    & pwsh -NoProfile -File (Join-Path $HarnessRoot 'install.ps1') $Scratch *> $installLog
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: install.ps1 returned $LASTEXITCODE"
        Get-Content -LiteralPath $installLog | ForEach-Object { "    $_" } | Write-Host
        exit 1
    }

    # ── (b) scratch/wiki/ byte-for-byte == templates/wiki/ ──────────────────
    Write-Host '==> [b] scratch/wiki/ byte-for-byte == templates/wiki/'
    $tplRoot     = (Resolve-Path -LiteralPath (Join-Path $HarnessRoot 'templates/wiki')).ProviderPath
    $scratchWiki = (Resolve-Path -LiteralPath (Join-Path $Scratch     'wiki')).ProviderPath

    function Get-TreeMap([string]$root) {
        $map = @{}
        if (-not (Test-Path -LiteralPath $root)) { return $map }
        Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
            $map[$rel] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
        return $map
    }

    $tplMap     = Get-TreeMap $tplRoot
    $scratchMap = Get-TreeMap $scratchWiki

    $diffs = @()
    foreach ($k in $tplMap.Keys) {
        if (-not $scratchMap.ContainsKey($k)) { $diffs += "MISSING in scratch: $k" }
        elseif ($scratchMap[$k] -ne $tplMap[$k]) { $diffs += "DIFFERS: $k" }
    }
    foreach ($k in $scratchMap.Keys) {
        if (-not $tplMap.ContainsKey($k)) { $diffs += "EXTRA in scratch: $k" }
    }
    if ($diffs.Count -gt 0) {
        Write-Host 'FAIL: scratch/wiki/ differs from templates/wiki/'
        $diffs | ForEach-Object { "    $_" } | Write-Host
        exit 1
    }
    Write-Host "    tree-diff clean ($($tplMap.Count) files)"

    # ── (c) no HARNESS_ROOT/wiki/ content leaked into scratch/wiki/ ─────────
    Write-Host '==> [c] no HARNESS_ROOT/wiki/ content leaked into scratch/wiki/'
    $dogfoodWiki = Join-Path $HarnessRoot 'wiki'
    $hHarness   = @{}
    $hTemplates = @{}
    $hScratch   = @{}
    function Add-Hashes([hashtable]$dict, [string]$root) {
        if (-not (Test-Path -LiteralPath $root)) { return }
        Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object {
            $h = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            if (-not $dict.ContainsKey($h)) { $dict[$h] = @() }
            $dict[$h] += $_.FullName
        }
    }
    Add-Hashes $hHarness   $dogfoodWiki
    Add-Hashes $hTemplates $tplRoot
    Add-Hashes $hScratch   $scratchWiki

    # A leak = a hash present in $HarnessRoot/wiki/ AND scratch/wiki/, but NOT
    # also in templates/wiki/. (If a file happens to be byte-identical in both
    # templates/wiki/ and the dogfood wiki/, that's not a leak — scratch got it
    # from templates/.)
    $leaks = @()
    foreach ($h in $hHarness.Keys) {
        if ($hScratch.ContainsKey($h) -and -not $hTemplates.ContainsKey($h)) {
            $leaks += "sha=$($h.Substring(0, 12)) wiki=$($hHarness[$h] -join ',') scratch=$($hScratch[$h] -join ',')"
        }
    }
    if ($leaks.Count -gt 0) {
        Write-Host 'FAIL: $HarnessRoot/wiki/ content appears in scratch/wiki/:'
        $leaks | ForEach-Object { "    $_" } | Write-Host
        exit 1
    }
    Write-Host "    hashes clean ($($hHarness.Count) wiki files checked against $($hScratch.Count) scratch files)"

    # ── (d) wiki-sync workflow reached the scratch install ─────────────────
    Write-Host '==> [d] .github/workflows/wiki-sync.yml present'
    $wikiSync = Join-Path $Scratch '.github/workflows/wiki-sync.yml'
    if (-not (Test-Path -LiteralPath $wikiSync)) {
        Write-Host 'FAIL: .github/workflows/wiki-sync.yml missing from scratch install'
        exit 1
    }
    Write-Host '    present'

    # ── (e) boundary-guard fires on out-of-boundary Copy-ManagedFile source ─
    Write-Host '==> [e] Ensure-BoundarySrc guard fires on Defect-2 mutation'

    # Rewrite the wiki-sync Copy-ManagedFile source from templates/ to the
    # source-repo mirror. This is exactly the regression class the guard
    # must catch. Write bytes directly to preserve LF line endings — using
    # Set-Content on Windows would CRLF-normalize and balloon the diff.
    $origPath = (Resolve-Path -LiteralPath (Join-Path $HarnessRoot 'install.ps1')).ProviderPath
    $origText = [System.IO.File]::ReadAllText($origPath)
    $mutatedText = $origText -replace 'templates/\.github/workflows/wiki-sync\.yml', '.github/workflows/wiki-sync.yml'
    if ($mutatedText -eq $origText) {
        Write-Host 'FAIL: (e) setup — regex mutation produced no change; test is not exercising the guard'
        exit 1
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Mutated, $mutatedText, $utf8NoBom)

    $scratchE = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-testinstall-e-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $scratchE -Force | Out-Null
    $mutatedLog = Join-Path $Scratch '.mutated.log'
    try {
        & pwsh -NoProfile -File $Mutated $scratchE *> $mutatedLog
        $rc = $LASTEXITCODE
    } finally {
        Remove-PathQuiet $scratchE
    }

    if ($rc -eq 0) {
        Write-Host 'FAIL: (e) mutated install.ps1 succeeded — guard did not fire'
        Get-Content -LiteralPath $mutatedLog -ErrorAction SilentlyContinue | ForEach-Object { "    $_" } | Write-Host
        exit 1
    }
    $mutatedText = Get-Content -LiteralPath $mutatedLog -Raw -ErrorAction SilentlyContinue
    if (-not $mutatedText -or ($mutatedText -notmatch 'installer-boundary violation')) {
        Write-Host "FAIL: (e) mutated install.ps1 failed (exit=$rc) but not with boundary-violation message"
        if ($mutatedText) { $mutatedText -split "`n" | ForEach-Object { "    $_" } | Write-Host }
        exit 1
    }
    Write-Host "    guard fired (exit=$rc, boundary-violation message present)"

    Write-Host '==> test-install: OK'
}
finally {
    Remove-PathQuiet $Scratch
    Remove-PathQuiet $Mutated
}
