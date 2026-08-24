# agentm-update.ps1 — global PATH launcher for refreshing the agentm install.
#
# Per V4 #30 plan #22 task 5: Windows twin of templates/bin/agentm-update.
# Reads <install-prefix>/.agentm-config.json (or legacy
# `.agentm-install-state.json` on pre-v4.5.1 installs) + re-runs the recorded
# installer source. Pass-through for extra args.
#
# A bare re-run IS the refresh: there is one install scope, and the installer
# is idempotent. It previously passed `-Update -Scope user`; both parameters
# were retired when the per-project install was, and PowerShell now rejects
# them at bind time.

#Requires -Version 7.0
$ErrorActionPreference = 'Stop'

# Resolve install prefix. Honor AGENTM_INSTALL_PREFIX env if set; else ~/.claude
$InstallPrefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { Join-Path $HOME '.claude' }
$StateFile = Join-Path $InstallPrefix '.agentm-config.json'
$LegacyFile = Join-Path $InstallPrefix '.agentm-install-state.json'

# v4.5.1: prefer new filename; fall back to legacy on pre-migration installs.
if (-not (Test-Path $StateFile)) {
    if (Test-Path $LegacyFile) {
        $StateFile = $LegacyFile
    } else {
        Write-Error "agentm-update: no install state at $StateFile`n  Run install.ps1 first to bootstrap the user-scope install."
        exit 1
    }
}

try {
    $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
} catch {
    Write-Error "agentm-update: failed to parse $StateFile : $_"
    exit 1
}

$InstallerSource = $state.installer_source
if (-not $InstallerSource) {
    Write-Error "agentm-update: install-state has no 'installer_source' field`n  Re-run install.ps1 from your agentm clone (or a release tarball) to refresh."
    exit 1
}

if (-not (Test-Path $InstallerSource)) {
    Write-Error "agentm-update: recorded installer source not found at: $InstallerSource`n  Did the source clone move? Re-run install.ps1 from its current location."
    exit 1
}

# Invoke installer with pass-through args (e.g. -ForceVersionCheck, -Rollback).
& $InstallerSource @args
exit $LASTEXITCODE
