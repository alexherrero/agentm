# install-state-sync.ps1 — SessionStart hook (Windows twin of .sh).
# Per V4 #30 plan #22 task 6. Non-blocking; exit 0 always.

#Requires -Version 7.0
$ErrorActionPreference = 'SilentlyContinue'

# Locate Python
$pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pythonCmd) { exit 0 }

# Locate helper. Source clone takes precedence; then install-prefix.
$installPrefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { Join-Path $HOME '.claude' }

$candidates = @(
    Join-Path $HOME 'Antigravity/agentm/scripts/install_state_sync.py'
    Join-Path $installPrefix 'scripts/install_state_sync.py'
    Join-Path (Join-Path $installPrefix '..') 'share/agentm/scripts/install_state_sync.py'
)

$helper = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $helper = $c; break }
}
if (-not $helper) { exit 0 }

# Invoke
& $pythonCmd.Source $helper '--install-prefix' $installPrefix '--quiet' 2>&1
exit 0
