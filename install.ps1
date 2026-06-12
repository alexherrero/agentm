# install.ps1 — install or update agentm in a target project.
#
# Usage:
#   pwsh -NoProfile -File /path/to/agentm/install.ps1 [-Hooks] [-Update] <target-project-path>
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
    [switch]$ForceVaultPrompt,   # v4.5.1 task 4: re-fire first-run vault prompt
    [switch]$LocalState,         # Hardening I #44 task 4: repo-local (vault-less) state
    [ValidateSet('user', 'project')]
    [string]$Scope = 'project',
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

if ($Scope -eq 'project' -and -not $Target) {
    Write-Error 'Usage: install.ps1 [-Hooks] [-Update] [-LocalState] [-Scope user|project] <target-project-path>
  -Scope user: install customizations to ~/.claude/ (target not required)
  -Scope project (default): install to <target>/.claude/'
    exit 1
}

# Hardening I #44 task 4: -LocalState threads `--state-mode local` into the
# install-state persist call (both -Scope user and -Scope project) so
# .agentm-config.json becomes the on-host source of truth for repo-local,
# vault-less harness state (DC-8). Empty array splats to nothing when not set.
$PersistStateModeArgs = @()
if ($LocalState) { $PersistStateModeArgs = @('--state-mode', 'local') }

# ── crickets-sibling bootstrap: REMOVED (crickets v3.0 #40 part 5) ──────────
# agentm's installer no longer auto-clones + invokes crickets's install.ps1 —
# crickets dropped its bespoke per-host installer in favor of NATIVE plugins.
# Operators install crickets via its one-line bootstrap or the host's native
# `plugin install`. The two repos are now decoupled at install time.

# ── -Scope user dispatch (V4 #30 task 8) ────────────────────────────────────
if ($Scope -eq 'user') {
    $UserInstallPrefix = if ($env:AGENTM_INSTALL_PREFIX) {
        $env:AGENTM_INSTALL_PREFIX
    } else {
        Join-Path $HOME '.claude'
    }
    New-Item -ItemType Directory -Path $UserInstallPrefix -Force | Out-Null
    Write-Host "==> installing agentm (-Scope user) into: $UserInstallPrefix (version $HarnessVersion)"

    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $pythonCmd) {
        Write-Error '-Scope user requires python3 on PATH'
        exit 1
    }

    $installStatePy = Join-Path $HarnessRoot 'lib/install/python/install_state.py'
    $installSymlinksPy = Join-Path $HarnessRoot 'lib/install/python/install_symlinks.py'
    $installCopyPy = Join-Path $HarnessRoot 'lib/install/python/install_copy.py'

    # Detect install mode
    $detectJson = & $pythonCmd.Source $installStatePy 'detect' 2>$null
    $mode = 'release'
    try {
        $detect = $detectJson | ConvertFrom-Json
        if ($detect.mode) { $mode = $detect.mode }
    } catch { }
    Write-Host "    install mode: $mode"

    if ($mode -eq 'source') {
        $args = @($UserInstallPrefix)
        $agentmClone = Join-Path $HOME 'Antigravity/agentm'
        if (Test-Path $agentmClone) { $args += '--agentm'; $args += $agentmClone }
        & $pythonCmd.Source $installSymlinksPy @args | Out-Null
        Write-Host '    symlinks: created'
    } else {
        # Release-mode copy from this harness's source tree
        foreach ($srcSubdir in @('harness/agents', 'harness/skills', 'harness/hooks', 'adapters/claude-code')) {
            $srcPath = Join-Path $HarnessRoot $srcSubdir
            if (Test-Path $srcPath) {
                & $pythonCmd.Source $installCopyPy $srcPath $UserInstallPrefix *>$null
            }
        }
        Write-Host '    customizations: copied'
    }

    # Persist install state
    & $pythonCmd.Source $installStatePy 'persist' `
        $UserInstallPrefix `
        '--harness-version' $HarnessVersion `
        '--installer-source' (Join-Path $HarnessRoot 'install.ps1') `
        @PersistStateModeArgs | Out-Null

    # Install agentm-update launcher
    $userBin = Join-Path $HOME '.local/bin'
    New-Item -ItemType Directory -Path $userBin -Force | Out-Null
    $launcherSrc = Join-Path $HarnessRoot 'templates/bin/agentm-update.ps1'
    if (Test-Path $launcherSrc) {
        Copy-Item -LiteralPath $launcherSrc -Destination (Join-Path $userBin 'agentm-update.ps1') -Force
        Write-Host "    launcher: $userBin\agentm-update.ps1 (add ~/.local/bin to PATH if not already)"
    }

    # ── v4.5.1 task 4: first-run vault detection ─────────────────────────────
    # CI runners + non-Darwin hosts auto-skip (Windows operators currently use
    # manual `agentm_config.py --vault-path <path>`; macOS auto-detect on
    # PowerShell is deferred — no operator running PowerShell on macOS today).
    if ($LocalState) {
        Write-Host "    state_mode: local (repo-local, vault-less); skipping vault detection"
    } elseif ($env:CI -eq 'true') {
        Write-Host "    vault prompt: CI detected; skipping (set via agentm_config.py --vault-path if needed)"
    } else {
        $configCli = Join-Path $HarnessRoot 'scripts/agentm_config.py'
        $env:AGENTM_INSTALL_PREFIX = $UserInstallPrefix
        $existing = ''
        try {
            $existing = (& $pythonCmd.Source $configCli '--get' 'vault_path' 2>$null | Out-String).Trim()
        } catch { }
        $env:AGENTM_INSTALL_PREFIX = $null
        if ($existing -and -not $ForceVaultPrompt) {
            Write-Host "    vault_path: $existing (use -ForceVaultPrompt to re-select)"
        } else {
            # Cross-platform auto-detect is out of scope for v4.5.1 (locked DC-7,
            # macOS-only). pwsh-on-macOS is rare; pwsh-on-Windows operators set
            # the path manually until a follow-up adds Windows-side detection.
            Write-Host "    vault prompt: pwsh host; auto-detect not yet implemented"
            Write-Host "    set the vault path manually via:"
            Write-Host "      python3 $configCli --vault-path <path-to-your-Obsidian-vault>"
        }
    }

    # Antigravity GLOBAL rules (V4 #22 Task 4b) — the user-scope Antigravity
    # channel, parity with ~/.claude/. Merge the AgentMemory vault-usage payload
    # into ~/.gemini/GEMINI.md (Antigravity's global rules file, applied across
    # every workspace) as a managed section so Antigravity picks up the vault
    # everywhere without a per-project install. Only when ~/.gemini/ exists (the
    # operator runs Antigravity/Gemini). Idempotent; preserves the operator's own
    # GEMINI.md. Source = the Antigravity workspace rule body; ONLY
    # agentmemory-context goes global — harness.md is a per-project contract.
    $geminiDir = Join-Path $HOME '.gemini'
    if (Test-Path -LiteralPath $geminiDir -PathType Container) {
        $agentmemorySrc = Join-Path $HarnessRoot 'adapters/antigravity/rules/agentmemory-context.md'
        if (Test-Path -LiteralPath $agentmemorySrc -PathType Leaf) {
            $geminiMd = Join-Path $geminiDir 'GEMINI.md'
            $mergeScript = Join-Path $HarnessRoot 'scripts/merge-managed-section.py'
            Write-Host '    Antigravity global rules -> ~/.gemini/GEMINI.md'
            & $pythonCmd.Source $mergeScript $geminiMd $agentmemorySrc '--marker' 'AGENTMEMORY' '--strip-frontmatter'
            if ($LASTEXITCODE -ne 0) {
                Write-Warning '    failed to merge agentmemory-context into ~/.gemini/GEMINI.md (continuing)'
            }
        }
    }

    Write-Host '==> done (-Scope user)'
    exit 0
}

# ── -Scope project (default; legacy per-project install) ────────────────────
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
        Write-Host "==> updating agentm in: $Target ($ExistingVersion -> $HarnessVersion)"
    } elseif ($ExistingVersion) {
        Write-Host "==> updating agentm in: $Target (already at $HarnessVersion; refreshing managed files)"
    } else {
        Write-Host "==> updating agentm in: $Target (no prior version recorded; treating as fresh refresh)"
    }
} else {
    Write-Host "==> installing agentm into: $Target (version $HarnessVersion)"
    if ($ExistingVersion -and $ExistingVersion -ne $HarnessVersion) {
        Write-Host "    note: this project is on $ExistingVersion; harness is $HarnessVersion."
        Write-Host "          re-run with -Update to refresh harness-authored files."
    }
}

# ── shared install plumbing ────────────────────────────────────────────────
#
# Install primitives (Ensure-BoundarySrc, Copy-UserFile, Copy-ManagedFile,
# Copy-UserWalk, Copy-ManagedDir, Copy-AdapterFiles, Copy-AdapterDirs,
# Sync-ManagedParents) live in lib/install/pwsh/primitives.ps1 and are
# byte-identical to crickets's copy. See lib/install/CONTRACT.md for
# the caller contract.
#
# Caller-set script-scope variables the lib reads:
#   $Update          switch — managed-copy functions overwrite when set
#   $BoundaryRoots   string[] — Ensure-BoundarySrc accepts sources only
#                    from under these roots

$BoundaryRoots = @(
    (Join-Path $HarnessRoot 'templates'),
    (Join-Path $HarnessRoot 'adapters'),
    # V4 #36: compound customizations imported from crickets live at
    # harness/{skills,hooks,agents,plugins}/. The new dispatcher block
    # (see "compound customizations" section below) reads from these.
    (Join-Path $HarnessRoot 'harness/skills'),
    (Join-Path $HarnessRoot 'harness/hooks'),
    (Join-Path $HarnessRoot 'harness/agents')
)

. (Join-Path $HarnessRoot 'lib/install/pwsh/primitives.ps1')

# ── -Update sync: wipe fully-managed adapter dirs before recreate ───────────
#
# Why: Copy-ManagedDir refreshes content but never removes a dir deleted from
# source (e.g. when an adapter is dropped — codex/v0.10.0). Without this
# wipe, -Update leaves orphan files from the previous version's adapter set
# and the local tree drifts from the GitHub source-of-truth.
#
# Safe to wipe: these subdirs contain ONLY harness-authored content. User
# customizations go in crickets (roadmap #1) or user-global ~/.claude/
# paths. File-level user state at .harness/ root (PLAN.md, features.json,
# progress.md, etc.) and settings.json files are not in the dirs below.
$ManagedParents = @(
    '.claude/commands', '.claude/agents', '.claude/skills',
    # V4 #36: compound hooks (memory-*, harness-context-session-start)
    # imported from crickets land at .claude/hooks/ via the manifest dispatcher.
    '.claude/hooks',
    # legacy (pre-V4 #22) — wiped on -Update; migrated to .agents/
    '.agent/rules', '.agent/workflows', '.agent/skills',
    '.agents/rules', '.agents/workflows', '.agents/skills',
    '.codex/agents',
    '.gemini/commands', '.gemini/agents',
    '.harness/scripts', '.harness/hooks'
)
$EmptyParentCandidates = @('.codex', '.agent', '.agents')

if ($Update) {
    Write-Host '==> sync mode: wiping fully-managed dirs before recreate from source'
    Sync-ManagedParents $ManagedParents $EmptyParentCandidates
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

# .agents/ — Antigravity adapter (V4 #22: migrated .agent/ → .agents/, the
# Antigravity 2.0 default; legacy .agent/ wiped on -Update). Only the always-on
# rules ship here now — the phase-gated dev-loop workflows + review sub-agents
# (formerly adapters/antigravity/{workflows,skills}) were slimmed out in the V5
# unbundling; that surface is provided by the crickets developer-workflows /
# code-review plugins. Shared skills (doctor, migrate-to-diataxis) still land in
# .agents/skills/ via the dedicated delivery loop below.
Copy-AdapterFiles (Join-Path $HarnessRoot 'adapters/antigravity/rules')     '*.md' '.agents/rules'

# .agents/skills/ — shared skills delivery (read by Gemini CLI per the
# Agent Skills standard). Source: adapters/claude-code/skills/ (parity
# enforces identical content; cleanest source — antigravity/skills/
# mixes sub-agents-as-skills with shared skills).
New-Item -ItemType Directory -Path '.agents/skills' -Force | Out-Null
foreach ($name in @('doctor', 'migrate-to-diataxis')) {
    $src = Join-Path $HarnessRoot "adapters/claude-code/skills/$name"
    if (Test-Path -LiteralPath $src -PathType Container) {
        Copy-ManagedDir $src (Join-Path '.agents/skills' $name)
    }
}

# .gemini/ — Gemini CLI adapter. Reads shared skills from .agents/skills/
# (delivered above) and the AGENTS.md context wiring from settings.json. The
# phase-command + sub-agent surfaces (formerly adapters/gemini/{commands,agents})
# were slimmed out in the V5 unbundling — that dev loop is provided by the
# crickets developer-workflows / code-review plugins, not vendored here.
New-Item -ItemType Directory -Path '.gemini' -Force | Out-Null
Copy-UserFile     (Join-Path $HarnessRoot 'adapters/gemini/settings.json') '.gemini/settings.json'

# ── wiki/ scaffold (per-file walk, skip-if-exists) ──────────────────────────
Copy-UserWalk (Join-Path $HarnessRoot 'templates/wiki') 'wiki'

# ── compound customizations (V4 #36) ───────────────────────────────────────
#
# Walks harness/skills/<dir>/SKILL.md, harness/hooks/<dir>/hook.md, and
# harness/agents/<file>.md, dispatching each based on its supported_hosts
# field. Imported from crickets in v4.0.0 per design call #28 of plan #18:
# compound skills (memory, design, ship-release), memory
# hooks (memory-recall-*, memory-reflect-*, conflict-merger-session-start,
# harness-context-session-start), and the memory-idea-researcher +
# adapt-evaluator sub-agents.
#
# Only dispatches entries with crickets-shape frontmatter (kind: <type> +
# supported_hosts: <list>). Legacy agentm single-file skills (doctor.md,
# migrate-to-diataxis.md) at harness/skills/*.md without frontmatter flow
# through the adapters/ pipeline above and are skipped here.

function Get-AmManifestField([string]$File, [string]$Field) {
    # Cheap YAML field extractor — independent of pyyaml.
    if (-not (Test-Path -LiteralPath $File)) { return '' }
    $inFm = $false
    $firstDashSeen = $false
    foreach ($line in (Get-Content -LiteralPath $File)) {
        if ($line -match '^---\s*$') {
            if (-not $firstDashSeen) { $firstDashSeen = $true; $inFm = $true; continue }
            break
        }
        if ($inFm -and $line -match "^$([regex]::Escape($Field))\s*:\s*(.*)$") {
            $val = $Matches[1]
            $val = $val -replace '\s*#.*$', ''
            $val = $val.Trim()
            $val = $val -replace '^\[|\]$', ''
            return $val.Trim()
        }
    }
    return ''
}

function Invoke-AmDispatchSkill([string]$SkillDir, [string]$Name, [string]$Hosts) {
    foreach ($h in ($Hosts -split ',')) {
        $h = $h.Trim()
        if (-not $h) { continue }
        switch ($h) {
            'claude-code' {
                New-Item -ItemType Directory -Path '.claude/skills' -Force | Out-Null
                Copy-ManagedDir $SkillDir (Join-Path '.claude/skills' $Name)
            }
            'antigravity' {
                New-Item -ItemType Directory -Path '.agents/skills' -Force | Out-Null
                Copy-ManagedDir $SkillDir (Join-Path '.agents/skills' $Name)
            }
            default {
                Write-Warning "skill '$Name': unknown host '$h' - skipped"
            }
        }
    }
}

function Invoke-AmDispatchHook([string]$HookDir, [string]$Name, [string]$Hosts) {
    foreach ($h in ($Hosts -split ',')) {
        $h = $h.Trim()
        if (-not $h) { continue }
        switch ($h) {
            'claude-code' {
                New-Item -ItemType Directory -Path '.claude/hooks' -Force | Out-Null
                $scriptSrc = Join-Path $HookDir "$Name.ps1"
                if (-not (Test-Path -LiteralPath $scriptSrc)) {
                    Write-Warning "hook '$Name' missing $Name.ps1 - skipped"
                    continue
                }
                Copy-ManagedFile $scriptSrc (Join-Path '.claude/hooks' "$Name.ps1")
                # Copy sibling .py helpers (memory-recall/reflect pattern).
                Get-ChildItem -Path $HookDir -Filter '*.py' -File -ErrorAction SilentlyContinue | ForEach-Object {
                    Copy-ManagedFile $_.FullName (Join-Path '.claude/hooks' $_.Name)
                }
                # Merge pwsh settings fragment idempotently.
                $frag = Join-Path $HookDir 'settings-fragment-pwsh.json'
                if ((Test-Path -LiteralPath $frag) -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
                    New-Item -ItemType Directory -Path '.claude' -Force | Out-Null
                    try {
                        & python3 (Join-Path $HarnessRoot 'scripts/merge-settings-fragment.py') '.claude/settings.json' $frag 2>$null
                    } catch {
                        Write-Warning "failed to merge settings fragment for hook '$Name': $_"
                    }
                }
            }
            'antigravity' {
                # Antigravity has no first-class hook surface (ADR 0009).
                # Silently skip — hook author opted into both hosts; we
                # honor claude-code and no-op the antigravity side.
            }
            default {
                Write-Warning "hook '$Name': unknown host '$h' - skipped"
            }
        }
    }
}

function Invoke-AmDispatchAgent([string]$AgentMd, [string]$Name, [string]$Hosts) {
    foreach ($h in ($Hosts -split ',')) {
        $h = $h.Trim()
        if (-not $h) { continue }
        switch ($h) {
            'claude-code' {
                New-Item -ItemType Directory -Path '.claude/agents' -Force | Out-Null
                Copy-ManagedFile $AgentMd (Join-Path '.claude/agents' "$Name.md")
            }
            'antigravity' {
                # Sub-agent-as-skill pattern.
                $wrap = Join-Path '.agents/skills' $Name
                New-Item -ItemType Directory -Path $wrap -Force | Out-Null
                Copy-ManagedFile $AgentMd (Join-Path $wrap 'SKILL.md')
            }
            default {
                Write-Warning "agent '$Name': unknown host '$h' - skipped"
            }
        }
    }
}

# Walk compound skills.
$amSkillsRoot = Join-Path $HarnessRoot 'harness/skills'
if (Test-Path -LiteralPath $amSkillsRoot -PathType Container) {
    Get-ChildItem -LiteralPath $amSkillsRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $d = $_.FullName
        $manifest = Join-Path $d 'SKILL.md'
        if (-not (Test-Path -LiteralPath $manifest)) { return }
        $kind = Get-AmManifestField $manifest 'kind'
        if ($kind -ne 'skill') { return }
        $name = $_.Name
        $hosts = Get-AmManifestField $manifest 'supported_hosts'
        if (-not $hosts) {
            Write-Warning "skill '$name' has no supported_hosts - skipped"
            return
        }
        Write-Host "==> installing compound skill: $name"
        Invoke-AmDispatchSkill $d $name $hosts
    }
}

# Walk compound hooks.
$amHooksRoot = Join-Path $HarnessRoot 'harness/hooks'
if (Test-Path -LiteralPath $amHooksRoot -PathType Container) {
    Get-ChildItem -LiteralPath $amHooksRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $d = $_.FullName
        $manifest = Join-Path $d 'hook.md'
        if (-not (Test-Path -LiteralPath $manifest)) { return }
        $kind = Get-AmManifestField $manifest 'kind'
        if ($kind -ne 'hook') { return }
        $name = $_.Name
        $hosts = Get-AmManifestField $manifest 'supported_hosts'
        if (-not $hosts) {
            Write-Warning "hook '$name' has no supported_hosts - skipped"
            return
        }
        Write-Host "==> installing compound hook: $name"
        Invoke-AmDispatchHook $d $name $hosts
    }
}

# Walk compound agents. Only crickets-shape manifests (with kind: agent
# in frontmatter) are dispatched; legacy agentm sub-agents at
# harness/agents/*.md without frontmatter flow through adapters/.
$amAgentsRoot = Join-Path $HarnessRoot 'harness/agents'
if (Test-Path -LiteralPath $amAgentsRoot -PathType Container) {
    Get-ChildItem -LiteralPath $amAgentsRoot -Filter '*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
        $f = $_.FullName
        $kind = Get-AmManifestField $f 'kind'
        if ($kind -ne 'agent') { return }
        $name = $_.BaseName
        $hosts = Get-AmManifestField $f 'supported_hosts'
        if (-not $hosts) {
            Write-Warning "agent '$name' has no supported_hosts - skipped"
            return
        }
        Write-Host "==> installing compound agent: $name"
        Invoke-AmDispatchAgent $f $name $hosts
    }
}

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

# ── probe + persist install state (V4 #30 task 3) ──────────────────────────
# Detect whether the operator has source-clone canonical paths for agentm +
# crickets; persist the decision to <install-prefix>/.agentm-install-state.json.
# Silent — no stdout (helper output redirected). Decision drives the source-
# vs-release dispatch in tasks 4-5.

$pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
if ($pythonCmd) {
    try {
        & $pythonCmd.Source (Join-Path $HarnessRoot 'lib/install/python/install_state.py') 'persist' `
            '.claude' '--harness-version' $HarnessVersion `
            '--installer-source' (Join-Path $HarnessRoot 'install.ps1') `
            @PersistStateModeArgs *>$null
    } catch {
        # Silent failure — install proceeds; install-state.json is best-effort
    }
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
        Write-Host '  3. Run /doctor (Claude Code) to verify the install'
    } else {
        Write-Host '  2. Run /doctor (Claude Code) to verify the install'
    }
}
