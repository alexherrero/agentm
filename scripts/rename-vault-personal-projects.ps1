# rename-vault-personal-projects.ps1 — Windows twin of the bash renamer.
#
# Renames <vault>/personal-projects/ → <vault>/projects/ + sed sweep across
# always-load entries, project _index.md frontmatter, and wikilinks. Idempotent.
#
# Usage:
#   pwsh -NoProfile -File rename-vault-personal-projects.ps1 [-VaultPath <path>] [-Preview] [-Help]
#
# Per plan #20 task 5 / plan #18 design 05-state-migration.md § (a).

[CmdletBinding()]
param(
    [string]$VaultPath = $env:MEMORY_VAULT_PATH,
    [switch]$Preview,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Get-Content $PSCommandPath | Where-Object { $_ -match '^#' -or $_ -eq '' } | ForEach-Object { $_ -replace '^# ?', '' }
    exit 0
}

# ── vault resolution ──────────────────────────────────────────────────────
if (-not $VaultPath) {
    Write-Error 'vault path not provided. Set MEMORY_VAULT_PATH or pass -VaultPath.'
    exit 1
}
if (-not (Test-Path -LiteralPath $VaultPath -PathType Container)) {
    Write-Error "vault path is not a directory: $VaultPath"
    exit 1
}
$VaultPath = (Resolve-Path -LiteralPath $VaultPath).ProviderPath

$oldDir = Join-Path $VaultPath 'personal-projects'
$newDir = Join-Path $VaultPath 'projects'

# ── idempotency check ─────────────────────────────────────────────────────
$hasNew = Test-Path -LiteralPath $newDir -PathType Container
$hasOld = Test-Path -LiteralPath $oldDir -PathType Container

if ($hasNew -and -not $hasOld) {
    Write-Host '==> vault already renamed (projects/ exists, personal-projects/ absent). Nothing to do.'
    exit 0
}
if ($hasNew -and $hasOld) {
    Write-Error "BOTH $newDir AND $oldDir exist. Operator must resolve manually."
    exit 1
}
if (-not $hasOld) {
    Write-Error "neither $newDir nor $oldDir exists at vault root. Is this the correct vault?"
    exit 1
}

if ($Preview) {
    Write-Host '==> [PREVIEW MODE — no changes will be made]'
}
Write-Host '==> vault rename: personal-projects/ → projects/'
Write-Host "    vault: $VaultPath"
Write-Host "    old:   $oldDir"
Write-Host "    new:   $newDir"
Write-Host ''

# ── helper: in-place substitution ─────────────────────────────────────────
function Update-FileContent {
    param([string]$Path, [string]$Pattern, [string]$Replacement)
    $text = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    $new = $text.Replace($Pattern, $Replacement)
    if ($new -ne $text) {
        Set-Content -LiteralPath $Path -Value $new -Encoding utf8 -NoNewline
        Write-Host "    rewrote: $Path"
    }
}

# ── step 1: rename ────────────────────────────────────────────────────────
if ($Preview) {
    Write-Host "  WOULD: Move-Item `"$oldDir`" `"$newDir`""
} else {
    Move-Item -LiteralPath $oldDir -Destination $newDir
    Write-Host '  renamed: personal-projects/ → projects/'
}

# ── step 2: collect sweep targets ─────────────────────────────────────────
function Get-SweepTargets {
    $alwaysLoad = Join-Path $VaultPath 'personal-private/_always-load'
    $privateRoot = Join-Path $VaultPath 'personal-private'
    # In preview mode, the mv hasn't run; project files live under $oldDir.
    # In live mode, the mv ran above so they live under $newDir.
    $projectRoot = if (Test-Path -LiteralPath $newDir -PathType Container) { $newDir } else { $oldDir }

    $targets = @()
    if (Test-Path -LiteralPath $alwaysLoad) {
        $targets += Get-ChildItem -LiteralPath $alwaysLoad -Filter '*.md' -File -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $privateRoot) {
        $targets += Get-ChildItem -LiteralPath $privateRoot -Filter '*.md' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '[\\/]_archive[\\/]' }
    }
    if (Test-Path -LiteralPath $projectRoot) {
        $targets += Get-ChildItem -LiteralPath $projectRoot -Filter '*.md' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '[\\/]_archive[\\/]' -and $_.Name -notmatch '^PLAN\.archive\.' }
    }
    return $targets | Sort-Object -Property FullName -Unique
}

# ── step 3: sweep ─────────────────────────────────────────────────────────
if ($Preview) {
    Write-Host ''
    Write-Host '  WOULD sed-sweep personal-projects/ → projects/ across:'
    $count = 0
    foreach ($f in Get-SweepTargets) {
        $text = Get-Content -LiteralPath $f.FullName -Raw -Encoding utf8 -ErrorAction SilentlyContinue
        if ($text -match 'personal-projects') {
            Write-Host "    $($f.FullName)"
            $count++
        }
    }
    Write-Host ''
    Write-Host "  WOULD rewrite $count file(s)."
} else {
    Write-Host ''
    Write-Host '==> sed sweep: personal-projects → projects'
    $rewrote = 0
    $seen = 0
    foreach ($f in Get-SweepTargets) {
        $seen++
        $text = Get-Content -LiteralPath $f.FullName -Raw -Encoding utf8 -ErrorAction SilentlyContinue
        if ($text -match 'personal-projects') {
            Update-FileContent -Path $f.FullName -Pattern 'personal-projects/' -Replacement 'projects/'
            Update-FileContent -Path $f.FullName -Pattern 'personal-projects' -Replacement 'projects'
            $rewrote++
        }
    }
    Write-Host ''
    Write-Host "==> swept $seen file(s); rewrote $rewrote file(s)."

    # ── post-run integrity check ──────────────────────────────────────────
    Write-Host ''
    Write-Host '==> post-run integrity check'
    $remaining = Get-ChildItem -LiteralPath $VaultPath -Filter '*.md' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '[\\/]_archive[\\/]' -and $_.Name -notmatch '^PLAN\.archive\.' } |
        ForEach-Object {
            $t = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
            if ($t -match 'personal-projects') { $_.FullName }
        }
    $remainingCount = if ($remaining) { @($remaining).Count } else { 0 }
    if ($remainingCount -gt 0) {
        Write-Warning "$remainingCount residual reference(s) to 'personal-projects' remain in non-archive markdown:"
        @($remaining) | Select-Object -First 10 | ForEach-Object { Write-Warning "  $_" }
        Write-Warning '(These may be intentional historical references. Review manually.)'
    } else {
        Write-Host "  clean — no 'personal-projects' references outside _archive/ + PLAN.archive."
    }

    Write-Host ''
    Write-Host '==> rename complete. Reload Obsidian to refresh its graph + sidebar.'
}
