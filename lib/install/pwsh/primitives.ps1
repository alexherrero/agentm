# lib/install/pwsh/primitives.ps1
#
# Shared install plumbing for the agentm / crickets sibling repos.
# This file is BYTE-IDENTICAL across both repos. Modifications go through
# `scripts/sync-lib.sh` (or sync-lib.ps1 if added) to keep checksums aligned.
# See CONTRACT.md for the invariants both repos depend on.
#
# Caller contract — these variables must be set in the caller's scope before
# any function here is invoked:
#
#   $Update           Switch parameter. When set, managed-copy functions
#                     overwrite existing destinations; when not set,
#                     skip-if-exists.
#   $BoundaryRoots    String array of absolute directory paths.
#                     Ensure-BoundarySrc accepts a source iff it lives under
#                     one of these roots.
#
# Functions exposed:
#   Ensure-BoundarySrc <src>
#   Copy-UserFile <src> <dst>
#   Copy-ManagedFile <src> <dst>
#   Copy-UserWalk <srcRoot> <dstRoot>
#   Copy-ManagedDir <src> <dst>
#   Copy-AdapterFiles <srcDir> <glob> <dstDir>
#   Copy-AdapterDirs <srcDir> <dstDir>
#   Sync-ManagedParents <managedDirs...> -- <emptyParentCandidates...>

# ── boundary guard ─────────────────────────────────────────────────────────
function Ensure-BoundarySrc([string]$src) {
    $srcFull = $null
    try { $srcFull = (Resolve-Path -LiteralPath $src -ErrorAction Stop).ProviderPath } catch { $srcFull = $src }
    $sep = [System.IO.Path]::DirectorySeparatorChar
    foreach ($root in $script:BoundaryRoots) {
        $rootFull = $null
        try { $rootFull = (Resolve-Path -LiteralPath $root -ErrorAction Stop).ProviderPath } catch { continue }
        if ($srcFull.StartsWith($rootFull + $sep)) { return }
    }
    $allowed = ($script:BoundaryRoots | ForEach-Object { "         $_/*" }) -join "`n"
    Write-Error "installer-boundary violation - cp source outside allowed roots:`n       src: $srcFull`n       allowed roots:`n$allowed"
    exit 1
}

# ── file-level copies ──────────────────────────────────────────────────────

# Copy-UserFile: copy only if destination is missing. For files the user owns.
function Copy-UserFile([string]$src, [string]$dst) {
    Ensure-BoundarySrc $src
    if (-not (Test-Path -LiteralPath $dst)) {
        $parent = Split-Path -Parent $dst
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $src -Destination $dst
        Write-Host "    created $dst"
    } else {
        Write-Host "    kept    $dst (exists)"
    }
}

# Copy-ManagedFile: in -Update mode, always overwrite. Otherwise, skip if exists.
function Copy-ManagedFile([string]$src, [string]$dst) {
    Ensure-BoundarySrc $src
    $parent = Split-Path -Parent $dst
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if ($script:Update -and (Test-Path -LiteralPath $dst)) {
        $same = $false
        try {
            $a = Get-FileHash -LiteralPath $src -Algorithm SHA256
            $b = Get-FileHash -LiteralPath $dst -Algorithm SHA256
            $same = ($a.Hash -eq $b.Hash)
        } catch { }
        if ($same) {
            Write-Host "    kept    $dst (up to date)"
        } else {
            Copy-Item -LiteralPath $src -Destination $dst -Force
            Write-Host "    updated $dst"
        }
    } elseif (-not (Test-Path -LiteralPath $dst)) {
        Copy-Item -LiteralPath $src -Destination $dst
        Write-Host "    created $dst"
    } else {
        Write-Host "    kept    $dst (exists — re-run with -Update to refresh)"
    }
}

# ── directory-level copies ─────────────────────────────────────────────────

# Copy-UserWalk: walk a source directory recursively and Copy-UserFile each.
function Copy-UserWalk([string]$srcRoot, [string]$dstRoot) {
    if (-not (Test-Path -LiteralPath $srcRoot)) { return }
    $srcFull = (Resolve-Path -LiteralPath $srcRoot).ProviderPath
    Get-ChildItem -LiteralPath $srcFull -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($srcFull.Length).TrimStart('\', '/')
        $dstFile = Join-Path $dstRoot $rel
        $parent = Split-Path -Parent $dstFile
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-UserFile $_.FullName $dstFile
    }
}

# Copy-ManagedDir: managed-update semantics for whole directories.
function Copy-ManagedDir([string]$src, [string]$dst) {
    Ensure-BoundarySrc $src
    if ($script:Update -and (Test-Path -LiteralPath $dst)) {
        Remove-Item -LiteralPath $dst -Recurse -Force
        Copy-Item -LiteralPath $src -Destination $dst -Recurse
        Write-Host "    updated $dst"
    } elseif (-not (Test-Path -LiteralPath $dst)) {
        $parent = Split-Path -Parent $dst
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $src -Destination $dst -Recurse
        Write-Host "    created $dst"
    } else {
        Write-Host "    kept    $dst (exists — re-run with -Update to refresh)"
    }
}

# Copy-AdapterFiles: iterate files matching a glob and Copy-ManagedFile each.
function Copy-AdapterFiles([string]$srcDir, [string]$glob, [string]$dstDir) {
    if (-not (Test-Path -LiteralPath $srcDir)) { return }
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $srcDir -Filter $glob -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-ManagedFile $_.FullName (Join-Path $dstDir $_.Name)
    }
}

# Copy-AdapterDirs: iterate subdirs and Copy-ManagedDir each.
function Copy-AdapterDirs([string]$srcDir, [string]$dstDir) {
    if (-not (Test-Path -LiteralPath $srcDir)) { return }
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $srcDir -Directory | ForEach-Object {
        Copy-ManagedDir $_.FullName (Join-Path $dstDir $_.Name)
    }
}

# ── -Update sync block ─────────────────────────────────────────────────────
#
# Wipe fully-managed parent dirs before recreate from source. The caller
# passes its repo-specific list of managed parent dirs and empty-parent
# candidates, separated by '--'.
#
# Usage:
#   Sync-ManagedParents @($managedDirs) @($emptyParentCandidates)
#
# (Unlike the bash version which uses '--' as an inline sentinel, the pwsh
# version takes two array arguments — pwsh handles arrays naturally.)
#
# Output (matches the pre-extraction inline behavior verbatim):
#   "    removed <p>/" for each wiped managed dir
#   "    removed empty <p>/" for each removed empty parent
#   "    wiped N managed dir(s); rebuilding from source" summary

function Sync-ManagedParents([string[]]$managedDirs, [string[]]$emptyParentCandidates) {
    $wiped = 0
    foreach ($p in $managedDirs) {
        if (Test-Path -LiteralPath $p -PathType Container) {
            Remove-Item -LiteralPath $p -Recurse -Force
            Write-Host "    removed $p/"
            $wiped++
        }
    }
    foreach ($p in $emptyParentCandidates) {
        if (Test-Path -LiteralPath $p -PathType Container) {
            if (-not (Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $p -Force
                Write-Host "    removed empty $p/"
            }
        }
    }
    Write-Host "    wiped $wiped managed dir(s); rebuilding from source"
}
