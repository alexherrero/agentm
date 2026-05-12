# install.ps1 — install or update agentic-harness in a target project.
#
# Usage:
#   pwsh -NoProfile -File /path/to/agentic-harness/install.ps1 [-Hooks] [-Update] <target-project-path>
#
# Options:
#   -Hooks    Install the PostToolUse/PreCompact/SessionStart hooks into
#             .claude/settings.json and copy hook scripts to .harness/hooks/.
#             Merges idempotently with any existing settings.
#   -Update   Refresh harness-authored files (commands, agents, skills,
#             hooks, scripts) to the current harness version. Leaves
#             user-authored files alone (PLAN.md, progress.md, verify.*,
#             init.sh, known-migrations.md, AGENTS.md, CLAUDE.md).
#             Writes .harness/.version so future -Update runs can show
#             the version delta.
#
# Without -Update: existing files are preserved (skip-if-exists).
# With -Update:    harness-authored files are overwritten; user files stay.
#
# Windows parity with install.sh is SEMANTIC not syntactic: produces the
# same file tree and the same merged .claude/settings.json. Uses
# PowerShell-native idioms (ConvertFrom-Json / ConvertTo-Json) instead of
# translating the jq pipeline 1:1.

[CmdletBinding()]
param(
    [switch]$Hooks,
    [switch]$Update,
    [Parameter(Position = 0)]
    [string]$Target
)

$ErrorActionPreference = 'Stop'

# Installer boundary: this script copies ONLY from $HarnessRoot/templates/
# and $HarnessRoot/adapters/. The top-level $HarnessRoot/wiki/ tree is
# this repo's own dogfooded documentation (how to use the harness) and
# must NEVER be propagated into target projects. Target projects get the
# empty scaffold from $HarnessRoot/templates/wiki/ instead. Do not add
# copy paths that read from $HarnessRoot/wiki/ or $HarnessRoot/.github/.
$HarnessRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$HarnessVersion = 'dev'
try {
    $v = & git -C $HarnessRoot describe --tags --abbrev=0 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { $HarnessVersion = $v.Trim() }
} catch { }

if (-not $Target) {
    Write-Error 'Usage: install.ps1 [-Hooks] [-Update] <target-project-path>'
    exit 1
}
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
    Write-Error "target directory does not exist: $Target"
    exit 1
}

Set-Location -LiteralPath $Target

# Detect existing install
$ExistingVersion = ''
$versionFile = Join-Path '.harness' '.version'
if (Test-Path -LiteralPath $versionFile) {
    $ExistingVersion = (Get-Content -LiteralPath $versionFile -Raw).Trim()
}

if ($Update) {
    if ($ExistingVersion -and $ExistingVersion -ne $HarnessVersion) {
        Write-Host "==> updating agentic-harness in: $Target ($ExistingVersion -> $HarnessVersion)"
    } elseif ($ExistingVersion) {
        Write-Host "==> updating agentic-harness in: $Target (already at $HarnessVersion; refreshing managed files)"
    } else {
        Write-Host "==> updating agentic-harness in: $Target (no prior version recorded; treating as fresh refresh)"
    }
} else {
    Write-Host "==> installing agentic-harness into: $Target (version $HarnessVersion)"
    if ($ExistingVersion -and $ExistingVersion -ne $HarnessVersion) {
        Write-Host "    note: this project is on $ExistingVersion; harness is $HarnessVersion."
        Write-Host "          re-run with -Update to refresh harness-authored files."
    }
}

# ── helpers ─────────────────────────────────────────────────────────────────

# Ensure-BoundarySrc: installer-boundary runtime guard. Every file copied into
# a target project must originate from $HarnessRoot/templates/ or
# $HarnessRoot/adapters/ — never from elsewhere in the harness repo (e.g.
# this repo's own wiki/, CI workflows under .github/workflows/tests-*.yml, or
# an active mirror of a template like .github/workflows/wiki-sync.yml that's
# byte-identical to its template by design). Makes out-of-boundary cp
# regressions fail loudly instead of silently propagating source-repo
# content into target projects.
function Ensure-BoundarySrc([string]$src) {
    $srcFull = $null
    try { $srcFull = (Resolve-Path -LiteralPath $src -ErrorAction Stop).ProviderPath } catch { $srcFull = $src }
    $tplFull = (Resolve-Path -LiteralPath (Join-Path $HarnessRoot 'templates')).ProviderPath
    $adpFull = (Resolve-Path -LiteralPath (Join-Path $HarnessRoot 'adapters')).ProviderPath
    $sep = [System.IO.Path]::DirectorySeparatorChar
    if ($srcFull.StartsWith($tplFull + $sep) -or $srcFull.StartsWith($adpFull + $sep)) { return }
    Write-Error "installer-boundary violation - cp source outside allowed roots:`n       src: $srcFull`n       allowed: `$HarnessRoot/templates/*, `$HarnessRoot/adapters/*"
    exit 1
}

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
    if ($Update -and (Test-Path -LiteralPath $dst)) {
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

# Copy-UserWalk: walk a source directory recursively and Copy-UserFile each.
# Preserves files the user has already created in the destination tree.
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

# Copy-ManagedDir: same -Update semantics for whole directories (e.g. skills).
function Copy-ManagedDir([string]$src, [string]$dst) {
    Ensure-BoundarySrc $src
    if ($Update -and (Test-Path -LiteralPath $dst)) {
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

# Copy-Adapter: iterate files matching a glob and Copy-ManagedFile into dstDir.
function Copy-AdapterFiles([string]$srcDir, [string]$glob, [string]$dstDir) {
    if (-not (Test-Path -LiteralPath $srcDir)) { return }
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $srcDir -Filter $glob -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-ManagedFile $_.FullName (Join-Path $dstDir $_.Name)
    }
}

function Copy-AdapterDirs([string]$srcDir, [string]$dstDir) {
    if (-not (Test-Path -LiteralPath $srcDir)) { return }
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $srcDir -Directory | ForEach-Object {
        Copy-ManagedDir $_.FullName (Join-Path $dstDir $_.Name)
    }
}

# ── user files: per-project state (never overwrite) ─────────────────────────

New-Item -ItemType Directory -Path '.harness' -Force | Out-Null
foreach ($f in @('PLAN.md', 'features.json', 'progress.md', 'init.sh', 'known-migrations.md')) {
    Copy-UserFile (Join-Path $HarnessRoot "templates/$f") ".harness/$f"
}

# ── managed files: harness-authored (overwrite with -Update) ────────────────

# .harness/scripts/ — helper scripts invoked by agents and phases.
# Ship BOTH .sh and .ps1 versions so mixed-OS teams get the right one.
Copy-AdapterFiles (Join-Path $HarnessRoot 'templates/scripts') '*.sh'  '.harness/scripts'
Copy-AdapterFiles (Join-Path $HarnessRoot 'templates/scripts') '*.ps1' '.harness/scripts'

# .claude/ — Claude Code config
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/claude-code/commands') '*.md' '.claude/commands'
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/claude-code/agents')   '*.md' '.claude/agents'
Copy-AdapterDirs  (Join-Path $HarnessRoot 'adapters/claude-code/skills')          '.claude/skills'

# .agent/ — Antigravity adapter
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/antigravity/rules')     '*.md' '.agent/rules'
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/antigravity/workflows') '*.md' '.agent/workflows'
Copy-AdapterDirs  (Join-Path $HarnessRoot 'adapters/antigravity/skills')           '.agent/skills'

# .agents/skills/ — shared skills delivery (read by Gemini CLI per the
# Agent Skills standard). Source: adapters/claude-code/skills/ (parity
# enforces identical content; cleanest source — antigravity/skills/
# mixes sub-agents-as-skills with shared skills).
New-Item -ItemType Directory -Path '.agents/skills' -Force | Out-Null
foreach ($name in @('dependabot-fixer', 'doctor', 'migrate-to-diataxis', 'ship-release')) {
    $src = Join-Path $HarnessRoot "adapters/claude-code/skills/$name"
    if (Test-Path -LiteralPath $src -PathType Container) {
        Copy-ManagedDir $src (Join-Path '.agents/skills' $name)
    }
}

# .gemini/ — Gemini CLI adapter. Reads shared skills from .agents/skills/ (delivered above).
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/gemini/commands') '*.toml' '.gemini/commands'
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/gemini/agents')   '*.md'   '.gemini/agents'
Copy-UserFile     (Join-Path $HarnessRoot 'adapters/gemini/settings.json') '.gemini/settings.json'

# ── wiki/ scaffold (per-file walk, skip-if-exists) ──────────────────────────
Copy-UserWalk (Join-Path $HarnessRoot 'templates/wiki') 'wiki'

# ── .github/workflows/wiki-sync.yml — managed ───────────────────────────────
Copy-ManagedFile (Join-Path $HarnessRoot 'templates/.github/workflows/wiki-sync.yml') '.github/workflows/wiki-sync.yml'

# ── top-level entrypoints (never overwrite) ─────────────────────────────────

if (-not (Test-Path -LiteralPath 'AGENTS.md')) {
    Copy-Item -LiteralPath (Join-Path $HarnessRoot 'AGENTS.md') -Destination 'AGENTS.md'
    Write-Host '    created AGENTS.md'
} else {
    Write-Host "    kept    AGENTS.md (exists — you may want to merge harness sections from $HarnessRoot/AGENTS.md)"
}

if (-not (Test-Path -LiteralPath 'CLAUDE.md')) {
    Copy-Item -LiteralPath (Join-Path $HarnessRoot 'CLAUDE.md') -Destination 'CLAUDE.md'
    Write-Host '    created CLAUDE.md'
} else {
    Write-Host '    kept    CLAUDE.md (exists)'
}

# ── -Hooks: install PostToolUse/PreCompact/SessionStart hooks ───────────────

if ($Hooks) {
    # verify.* — per-project (user-editable). Ship both .sh and .ps1.
    Copy-UserFile (Join-Path $HarnessRoot 'templates/verify.sh')  '.harness/verify.sh'
    Copy-UserFile (Join-Path $HarnessRoot 'templates/verify.ps1') '.harness/verify.ps1'

    # hook scripts — harness-authored (managed). Ship both .sh and .ps1.
    foreach ($f in @('precompact.sh', 'session-start-compact.sh',
                     'precompact.ps1', 'session-start-compact.ps1')) {
        Copy-ManagedFile (Join-Path $HarnessRoot "templates/hooks/$f") ".harness/hooks/$f"
    }

    # Hook fragment — PowerShell host picks the .ps1 fragment; bash picks .sh.
    # Installer boundary: canonical fragments live in templates/hooks/.
    # Use -AsHashtable throughout: PSCustomObject + ConvertTo-Json unwraps
    # single-element arrays into scalars, which would corrupt
    # hooks.PostToolUse (schema requires an array, not an object).
    $fragmentPath = Join-Path $HarnessRoot 'templates/hooks/settings-fragment-pwsh.json'
    $fragment = Get-Content -LiteralPath $fragmentPath -Raw | ConvertFrom-Json -AsHashtable

    $settingsPath = Join-Path '.claude' 'settings.json'
    if (-not (Test-Path -LiteralPath '.claude')) {
        New-Item -ItemType Directory -Path '.claude' -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $settingsPath)) {
        $fragment | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
        Write-Host '    created .claude/settings.json with harness hooks (verify + precompact + session-start)'
    } else {
        $existing = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json -AsHashtable
        if (-not $existing) { $existing = @{} }
        if (-not $existing.ContainsKey('hooks')) { $existing['hooks'] = @{} }
        $added = 0
        foreach ($eventName in $fragment.hooks.Keys) {
            # Each event's value is a list of matcher-group hashtables.
            $newEntry = @($fragment.hooks[$eventName])[0]
            $needle = @($newEntry.hooks)[0].command
            $existingForEvent = [System.Collections.ArrayList]::new()
            if ($existing.hooks.ContainsKey($eventName)) {
                foreach ($e in @($existing.hooks[$eventName])) { [void]$existingForEvent.Add($e) }
            }
            $already = $false
            foreach ($entry in $existingForEvent) {
                foreach ($h in @($entry.hooks)) {
                    if ($h.command -and ($h.command.ToString().Contains($needle))) {
                        $already = $true; break
                    }
                }
                if ($already) { break }
            }
            if (-not $already) {
                [void]$existingForEvent.Add($newEntry)
                # Store as List[object] so ConvertTo-Json emits a JSON array
                # even when the list has exactly one entry (PSCustomObject
                # + NoteProperty + single-element array would unwrap).
                $asList = [System.Collections.Generic.List[object]]::new()
                foreach ($e in $existingForEvent) { $asList.Add($e) }
                $existing.hooks[$eventName] = $asList
                $added++
            }
        }
        ($existing | ConvertTo-Json -Depth 20) | Set-Content -LiteralPath $settingsPath -Encoding UTF8
        if ($added -eq 0) {
            Write-Host '    kept    .claude/settings.json (all harness hooks already present)'
        } else {
            Write-Host '    updated .claude/settings.json (added missing harness hooks)'
        }
    }

    Write-Host ''
    Write-Host '==> hooks installed:'
    Write-Host '    - PostToolUse  -> .harness/verify.ps1 (per-file verification on Write/Edit)'
    Write-Host '    - PreCompact   -> .harness/hooks/precompact.ps1 (writes marker to progress.md)'
    Write-Host '    - SessionStart -> .harness/hooks/session-start-compact.ps1 (re-anchors after compact)'
    Write-Host '    Edit .harness/verify.ps1 to enable checks for your stack.'
}

# ── record version ──────────────────────────────────────────────────────────

Set-Content -LiteralPath (Join-Path '.harness' '.version') -Value $HarnessVersion

Write-Host ''
if ($Update) {
    Write-Host "==> update complete (now at $HarnessVersion)."
} else {
    Write-Host '==> done.'
    Write-Host ''
    Write-Host 'Next steps:'
    Write-Host '  1. Edit .harness/init.sh so it actually boots this project'
    if ($Hooks) {
        Write-Host '  2. Edit .harness/verify.ps1 — uncomment the extension case for your stack'
        Write-Host '  3. Run /setup (Claude Code) or prompt ''run the setup phase'' (Antigravity)'
        Write-Host '  4. Then /plan <your first brief>'
    } else {
        Write-Host '  2. Run /setup (Claude Code) or prompt ''run the setup phase'' (Antigravity)'
        Write-Host '  3. Then /plan <your first brief>'
    }
}
