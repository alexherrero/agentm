# install.ps1 — install agentm machine-wide, into ~/.claude/.
#
# Usage:
#   pwsh -NoProfile -File /path/to/agentm/install.ps1 [-LocalState]
#
# There is one install scope: this machine. Customizations land in
# $env:AGENTM_INSTALL_PREFIX (default ~/.claude/) and are shared by every
# project on the host. The per-project install (-Scope project) that copied a
# .claude/ tree into each target repo is retired; so is -Update, because
# re-running this installer IS the refresh.
#
# Options:
#   -LocalState  Opt this machine into repo-local (vault-less) harness state:
#             writes "state_mode": "local" to .agentm-config.json and skips
#             vault auto-detection.
#
# Windows parity with install.sh is SEMANTIC not syntactic: produces the
# same file tree and the same merged settings.json. Uses PowerShell-native
# idioms (ConvertFrom-Json / ConvertTo-Json) instead of translating the jq
# pipeline 1:1. The Go memory daemon is macOS-only (launchd), so install.sh's
# daemon section has no counterpart here.

[CmdletBinding()]
param(
    [switch]$ForceVaultPrompt,   # v4.5.1 task 4: re-fire first-run vault prompt
    [switch]$LocalState          # Hardening I #44 task 4: repo-local (vault-less) state
)

$ErrorActionPreference = 'Stop'

# Installer boundary: this script copies ONLY from $HarnessRoot/harness/,
# $HarnessRoot/adapters/, and $HarnessRoot/templates/. The top-level
# $HarnessRoot/wiki/ tree is this repo's own dogfooded documentation (how to
# use the harness) and must NEVER be installed. Do not add copy paths that
# read from $HarnessRoot/wiki/ or $HarnessRoot/.github/.
$HarnessRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$HarnessVersion = 'dev'
try {
    $v = & git -C $HarnessRoot describe --tags --abbrev=0 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { $HarnessVersion = $v.Trim() }
} catch { }

# Retired parameters. PowerShell rejects unknown named parameters on its own,
# so -Scope / -Update / -Hooks and a positional target now fail at bind time
# with a parameter-binding error rather than being silently ignored — the same
# outcome install.sh reaches through its explicit _retired_flag branches.

# Hardening I #44 task 4: -LocalState threads `--state-mode local` into the
# install-state persist call, so .agentm-config.json becomes the on-host source
# of truth for repo-local, vault-less harness state (DC-8). Empty array splats
# to nothing when not set.
$PersistStateModeArgs = @()
if ($LocalState) { $PersistStateModeArgs = @('--state-mode', 'local') }

# ── crickets-sibling bootstrap: REMOVED (crickets v3.0 #40 part 5) ──────────
# agentm's installer no longer auto-clones + invokes crickets's install.ps1 —
# crickets dropped its bespoke per-host installer in favor of NATIVE plugins.
# Operators install crickets via its one-line bootstrap or the host's native
# `plugin install`. The two repos are now decoupled at install time.

# ── merge installed hooks' settings fragments (GH #72) ──────────────────────
# pwsh twin of install.sh's _agentm_merge_user_hook_fragments (the V4 #39
# fix). The install (symlink/copy) drops hook DIRS into
# <prefix>/hooks/<name>/ but never merged their settings-fragment-pwsh.json
# into <prefix>/settings.json — so no hook ever fired on a Windows user-scope
# install. Walks the installed hook dirs, merges each pwsh fragment, and
# absolutizes the command to the user-scope layout ("pwsh -NoProfile -File
# <prefix>/hooks/<name>/<name>.ps1") — source fragments stay project-relative
# on disk; the command gets rewritten per scope, mirroring the bash side's
# same rule. Returns a {path, sha256} record array (as JSON) for the
# install-state fragments field. Idempotent: re-running merges nothing new.
function Merge-AgentmUserHookFragments {
    param([string]$Prefix, [string]$RecordsOutFile, $PythonCmd)
    $hooksDir = Join-Path $Prefix 'hooks'
    $records = @()
    $merged = 0
    if ((Test-Path -LiteralPath $hooksDir -PathType Container) -and $PythonCmd) {
        Get-ChildItem -LiteralPath $hooksDir -Directory | ForEach-Object {
            $name = $_.Name
            $frag = Join-Path $_.FullName 'settings-fragment-pwsh.json'
            $script = Join-Path $_.FullName "$name.ps1"
            if ((Test-Path -LiteralPath $frag) -and (Test-Path -LiteralPath $script)) {
                $settingsPath = Join-Path $Prefix 'settings.json'
                $command = "pwsh -NoProfile -File $script"
                & $PythonCmd.Source (Join-Path $HarnessRoot 'scripts/merge-settings-fragment.py') $settingsPath $frag '--command' $command 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $merged++
                    $sha = (Get-FileHash -LiteralPath $frag -Algorithm SHA256).Hash.ToLower()
                    $records += [ordered]@{ path = $frag; sha256 = $sha }
                } else {
                    Write-Warning "failed to merge settings fragment for user-scope hook '$name'"
                }
            }
        }
    }
    ($records | ConvertTo-Json -AsArray -Depth 5) | Set-Content -LiteralPath $RecordsOutFile
    Write-Host "    hooks: merged $merged settings fragment(s) into $Prefix\settings.json"
}

# ── install dispatch ────────────────────────────────────────────────────────
# Install customizations into $env:AGENTM_INSTALL_PREFIX (default ~/.claude/).
# Nothing per-project is created: state lives in the vault (V4 #26), and the
# per-project install flow is retired.
$UserInstallPrefix = if ($env:AGENTM_INSTALL_PREFIX) {
    $env:AGENTM_INSTALL_PREFIX
} else {
    Join-Path $HOME '.claude'
}
New-Item -ItemType Directory -Path $UserInstallPrefix -Force | Out-Null
Write-Host "==> installing agentm into: $UserInstallPrefix (version $HarnessVersion)"

$pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pythonCmd) {
    Write-Error 'agentm requires python3 on PATH'
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
    # Release-mode copy from this harness's source tree.
    # harness/{agents,skills,hooks} each need their own name as the
    # destination's top-level dir (install_copy.py relativizes against
    # source_dir, so copying straight into $UserInstallPrefix drops that
    # segment entirely — mirrors install_symlinks.py's explicit
    # "agents/{name}" / "skills/{name}" / "hooks/{name}" destination
    # mapping, the source-mode reference this release-mode path must
    # match).
    foreach ($srcSubdir in @('harness/agents', 'harness/skills', 'harness/hooks')) {
        $srcPath = Join-Path $HarnessRoot $srcSubdir
        if (Test-Path $srcPath) {
            $destPath = Join-Path $UserInstallPrefix (Split-Path -Leaf $srcSubdir)
            & $pythonCmd.Source $installCopyPy $srcPath $destPath *>$null
        }
    }
    # adapters/claude-code already nests commands/, skills/, agents/ as
    # its own immediate children — copying it straight into the prefix
    # is correct as-is, unlike the harness/* trio above.
    $adaptersPath = Join-Path $HarnessRoot 'adapters/claude-code'
    if (Test-Path $adaptersPath) {
        & $pythonCmd.Source $installCopyPy $adaptersPath $UserInstallPrefix *>$null
    }
    Write-Host '    customizations: copied'
}

# GH #72: merge installed hooks' settings fragments into settings.json —
# the missing pwsh half of the V4 #39 fix (bash side: install.sh's
# _agentm_merge_user_hook_fragments).
$fragRecordsFile = Join-Path ([System.IO.Path]::GetTempPath()) ("agentm-frag-" + [System.Guid]::NewGuid().ToString('N') + '.json')
Merge-AgentmUserHookFragments -Prefix $UserInstallPrefix -RecordsOutFile $fragRecordsFile -PythonCmd $pythonCmd

# Persist install state (incl. the merged-fragments records for drift detection)
& $pythonCmd.Source $installStatePy 'persist' `
    $UserInstallPrefix `
    '--harness-version' $HarnessVersion `
    '--installer-source' (Join-Path $HarnessRoot 'install.ps1') `
    '--fragments-file' $fragRecordsFile `
    @PersistStateModeArgs | Out-Null
Remove-Item -LiteralPath $fragRecordsFile -Force -ErrorAction SilentlyContinue

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
# ── done ────────────────────────────────────────────────────────────────────

Write-Host ''
Write-Host "==> done (agentm $HarnessVersion installed to $UserInstallPrefix)."
Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Run /doctor (Claude Code) to verify the install'
Write-Host '  2. Re-run this installer any time to refresh; it is idempotent'
