# agentm-update.ps1 — global PATH launcher for refreshing the user-scope agentm install.
#
# Per V4 #30 plan #22 task 5: Windows twin of templates/bin/agentm-update.
# Reads <install-prefix>/.agentm-install-state.json + invokes the recorded
# installer source with --update --scope user. Pass-through for extra args.

#Requires -Version 7.0
$ErrorActionPreference = 'Stop'

# Resolve install prefix. Honor AGENTM_INSTALL_PREFIX env if set; else ~/.claude
$InstallPrefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { Join-Path $HOME '.claude' }
$StateFile = Join-Path $InstallPrefix '.agentm-install-state.json'

if (-not (Test-Path $StateFile)) {
    Write-Error "agentm-update: no install state at $StateFile`n  Run install.ps1 first to bootstrap the user-scope install."
    exit 1
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
& $InstallerSource -Update -Scope user @args
exit $LASTEXITCODE
