# migrate-harness-to-vault.ps1 — Windows twin of the bash migration tool.
#
# Copies <target>/.harness/<file> → <vault>/projects/<slug>/_harness/<file>.
# Idempotent, reversible. See migrate-harness-to-vault.sh for full
# documentation; this script mirrors its surface area.
#
# Usage:
#   pwsh -NoProfile -File migrate-harness-to-vault.ps1 [OPTIONS] [TARGET]
#
# Options:
#   -VaultPath <path>   Override vault root. Default: $env:MEMORY_VAULT_PATH.
#   -Preview            Dry-run.
#   -Cleanup            Remove legacy files after byte-identical verification.
#   -Rollback           Write the repo-local .project-mode=local marker (DC-8).
#   -Yes                Skip confirmation prompts.
#   -Help               Print this help.
#
# Per plan #20 task 6 / plan #18 design 05-state-migration.md § (b).

[CmdletBinding()]
param(
    [string]$VaultPath = $env:MEMORY_VAULT_PATH,
    [switch]$Preview,
    [switch]$Cleanup,
    [switch]$Rollback,
    [switch]$Yes,
    [switch]$Help,
    [Parameter(Position=0)]
    [string]$Target
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Get-Content $PSCommandPath | Where-Object { $_ -match '^#' -or $_ -eq '' } | ForEach-Object { $_ -replace '^# ?', '' }
    exit 0
}

# ── resolve target + vault + slug ─────────────────────────────────────────
if (-not $Target) { $Target = $PWD.Path }
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
    Write-Error "target is not a directory: $Target"
    exit 1
}
$Target = (Resolve-Path -LiteralPath $Target).ProviderPath

if (-not $VaultPath) {
    Write-Error 'vault path not provided. Set MEMORY_VAULT_PATH or pass -VaultPath.'
    exit 1
}
if (-not (Test-Path -LiteralPath $VaultPath -PathType Container)) {
    Write-Error "vault path is not a directory: $VaultPath"
    exit 1
}
$VaultPath = (Resolve-Path -LiteralPath $VaultPath).ProviderPath

$Here = Split-Path -Parent $PSCommandPath
$VpPy = Join-Path $Here 'vault_project.py'
if (-not (Test-Path -LiteralPath $VpPy)) {
    Write-Error "vault_project.py not found at $VpPy"
    exit 1
}

$Slug = (& python3 $VpPy 'read' $Target 2>$null) -join ''
$Slug = $Slug.Trim()
if (-not $Slug) {
    Write-Error "could not resolve project slug from $Target. Set vault_project in .harness/project.json or add a git origin."
    exit 1
}

# Layout resolution: prefer projects/, fall back to personal-projects/.
$projectsNew = Join-Path $VaultPath 'projects'
$projectsLegacy = Join-Path $VaultPath 'personal-projects'
if (Test-Path -LiteralPath $projectsNew -PathType Container) {
    $ProjectDir = Join-Path $projectsNew $Slug
    $Segment = 'projects'
} elseif (Test-Path -LiteralPath $projectsLegacy -PathType Container) {
    $ProjectDir = Join-Path $projectsLegacy $Slug
    $Segment = 'personal-projects'
} else {
    $ProjectDir = Join-Path $projectsNew $Slug
    $Segment = 'projects'
}
$HarnessDir = Join-Path $ProjectDir '_harness'
$Marker = Join-Path $HarnessDir '.migrated-from-pre-v4.1'
$LegacyHarness = Join-Path $Target '.harness'
# The .project-mode marker is always repo-local (on-host) — config never lives
# in the vault (DC-8); both directions write here so the dispatcher's repo-local
# resolution layer sees it without a vault.
$ModeFile = Join-Path $LegacyHarness '.project-mode'

# ── rollback (mutually exclusive with migrate) ────────────────────────────
if ($Rollback) {
    Write-Host "==> rollback: setting .project-mode=local for project '$Slug'"
    Write-Host "    project root: $Target"
    Write-Host "    vault path:   $ProjectDir"
    if (-not (Test-Path -LiteralPath $HarnessDir)) {
        Write-Warning 'no vault-side _harness/ exists yet — nothing to roll back.'
        exit 0
    }
    if ($Preview) {
        Write-Host "  WOULD: Set-Content $ModeFile 'local'"
        exit 0
    }
    New-Item -ItemType Directory -Force -Path $LegacyHarness | Out-Null
    Set-Content -LiteralPath $ModeFile -Value 'local' -Encoding utf8 -NoNewline
    Write-Host "  set repo-local .project-mode=local. Dispatcher will now read from $LegacyHarness/"
    exit 0
}

# ── banner ────────────────────────────────────────────────────────────────
if ($Preview) { Write-Host '==> [PREVIEW MODE — no changes will be made]' }
Write-Host '==> migrate-harness-to-vault'
Write-Host "    target:       $Target"
Write-Host "    vault:        $VaultPath"
Write-Host "    project slug: $Slug"
Write-Host "    vault dest:   $HarnessDir/  (using '$Segment/' segment)"
Write-Host ''

# ── idempotency check ─────────────────────────────────────────────────────
if (Test-Path -LiteralPath $Marker -PathType Leaf) {
    $markerContent = Get-Content -LiteralPath $Marker -Raw
    $ts = ([regex]'(?m)^migrated_at:\s*(.+)$').Match($markerContent).Groups[1].Value
    Write-Host "==> already migrated on $ts — nothing to do."
    if (-not $Cleanup) { exit 0 }
    Write-Host '    (re-running with -Cleanup; will offer to remove legacy paths.)'
}

# ── state file mapping ────────────────────────────────────────────────────
$StateFiles = @(
    'PLAN.md', 'progress.md', 'ROADMAP.md',
    'ROADMAP-AgentMemoryV4.md', 'ROADMAP-AgentMemoryV5.md', 'ROADMAP-AgentMemoryV6.md',
    'FOLLOWUPS.md', 'features.json', 'init.sh', 'known-migrations.md',
    'verify.sh', 'verify.ps1', '.promoted-progress-cursor', 'project.json'
)
$StateDirs = @('designs', 'phases')

if (-not (Test-Path -LiteralPath $LegacyHarness)) {
    Write-Host "==> no legacy <target>/.harness/ found at $LegacyHarness"
    Write-Host '    (Nothing to migrate. If this is a fresh project, run /setup first.)'
    exit 0
}

if ($Preview) {
    Write-Host "  WOULD: New-Item -ItemType Directory -Path $HarnessDir -Force"
} else {
    New-Item -ItemType Directory -Path $HarnessDir -Force | Out-Null
}

$migrated = 0
$skipped = 0
$conflicts = 0

function Migrate-File {
    param([string]$Rel)
    $src = Join-Path $LegacyHarness $Rel
    $dst = Join-Path $HarnessDir $Rel
    if (-not (Test-Path -LiteralPath $src)) { return }

    if ($Rel -eq 'project.json') {
        Write-Warning '  [DEPRECATED] copying project.json (V4 #26 replaces it with vault _index.md frontmatter)'
    }

    if (Test-Path -LiteralPath $dst) {
        $srcHash = Get-FileHash -LiteralPath $src -Algorithm SHA256
        $dstHash = Get-FileHash -LiteralPath $dst -Algorithm SHA256
        if ($srcHash.Hash -eq $dstHash.Hash) {
            $script:skipped++
            return
        }
        $script:conflicts++
        Write-Warning "  CONFLICT: $Rel differs between legacy + vault. NOT overwriting."
        Write-Warning "    legacy: $src"
        Write-Warning "    vault:  $dst"
        return
    }

    if ($Preview) {
        Write-Host "  WOULD: Copy-Item $src $dst"
    } else {
        New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst
        Write-Host "    migrated: $Rel"
    }
    $script:migrated++
}

function Migrate-Dir {
    param([string]$Rel)
    $src = Join-Path $LegacyHarness $Rel
    $dst = Join-Path $HarnessDir $Rel
    if (-not (Test-Path -LiteralPath $src -PathType Container)) { return }

    if (Test-Path -LiteralPath $dst -PathType Container) {
        # Per-file conflict-aware migration.
        Get-ChildItem -LiteralPath $src -Recurse -File | ForEach-Object {
            $relrel = $_.FullName.Substring($src.Length + 1)
            Migrate-File -Rel (Join-Path $Rel $relrel)
        }
        return
    }

    $count = (Get-ChildItem -LiteralPath $src -Recurse -File).Count
    if ($Preview) {
        Write-Host "  WOULD: Copy-Item -Recurse $src $dst"
    } else {
        Copy-Item -LiteralPath $src -Destination $dst -Recurse
        Write-Host "    migrated: $Rel/ ($count file(s))"
    }
    $script:migrated += $count
}

Write-Host '==> copying state files'
foreach ($f in $StateFiles) { Migrate-File -Rel $f }

# PLAN.archive.*.md
Get-ChildItem -LiteralPath $LegacyHarness -Filter 'PLAN.archive.*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
    Migrate-File -Rel $_.Name
}

foreach ($d in $StateDirs) { Migrate-Dir -Rel $d }

# ── marker + project-mode ─────────────────────────────────────────────────
if (-not $Preview) {
    $markerBody = @"
# Migration marker — written by migrate-harness-to-vault.ps1
migrated_at: $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')
source: $LegacyHarness
target: $HarnessDir
slug: $Slug
files_migrated: $migrated
files_skipped_identical: $skipped
files_in_conflict: $conflicts
v4_26_plan: agentm v4.1.0
"@
    Set-Content -LiteralPath $Marker -Value $markerBody -Encoding utf8
    # Repo-local .project-mode=vault marker (DC-8) — per-repo override pinning
    # this migrated repo to vault reads even if the device default is local.
    Set-Content -LiteralPath $ModeFile -Value 'vault' -Encoding utf8 -NoNewline
}

Write-Host ''
Write-Host '==> migration summary'
Write-Host "    migrated:                 $migrated file(s)"
Write-Host "    skipped (already vault):  $skipped file(s)"
Write-Host "    conflicts:                $conflicts file(s)"
if (-not $Preview) {
    Write-Host "    marker:                   $Marker"
    Write-Host "    project mode:             $ModeFile → vault"
}

# ── cleanup ───────────────────────────────────────────────────────────────
if ($Cleanup -and -not $Preview) {
    Write-Host ''
    Write-Host '==> cleanup: removing legacy paths after byte-identical verification'
    if ($conflicts -gt 0) {
        Write-Error "ABORT: $conflicts conflict(s) detected. Resolve before cleanup."
        exit 1
    }
    if (-not $Yes) {
        $response = Read-Host '  proceed with cleanup? [y/N]'
        if ($response -ne 'y' -and $response -ne 'Y') {
            Write-Host '  cleanup skipped (operator declined).'
            exit 0
        }
    }
    $cleaned = 0
    foreach ($f in $StateFiles) {
        $src = Join-Path $LegacyHarness $f
        $dst = Join-Path $HarnessDir $f
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $srcHash = Get-FileHash -LiteralPath $src -Algorithm SHA256
        $dstHash = Get-FileHash -LiteralPath $dst -Algorithm SHA256
        if ($srcHash.Hash -eq $dstHash.Hash) {
            Remove-Item -LiteralPath $src
            $cleaned++
        }
    }
    Get-ChildItem -LiteralPath $LegacyHarness -Filter 'PLAN.archive.*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
        $dst = Join-Path $HarnessDir $_.Name
        $srcHash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
        $dstHash = Get-FileHash -LiteralPath $dst -Algorithm SHA256
        if ($srcHash.Hash -eq $dstHash.Hash) {
            Remove-Item -LiteralPath $_.FullName
            $cleaned++
        }
    }
    Write-Host "  cleaned $cleaned legacy file(s) (.evidence-reads + dirs preserved per DC-1)."
}

Write-Host ''
Write-Host '==> done.'
Write-Host ''
Write-Host 'Next steps:'
Write-Host "  - Inspect $HarnessDir/ in Obsidian."
Write-Host "  - To roll back: pwsh -NoProfile -File $PSCommandPath -Rollback $Target"
if (-not $Cleanup) {
    Write-Host '  - To remove legacy paths: re-run with -Cleanup after verifying.'
}
