# vault-verify (AgentM) — the vault's verify script: check a note when it is saved.
#
# PowerShell twin of templates/vault-verify.sh, installed beside it to
# <vault>/.harness/verify.ps1, which verify-dispatch.ps1 runs in preference to
# the bash one. The reasoning is the bash twin's; read that file. Exit 0 and
# silence on a clean note or anything out of scope, exit 2 with the problem on
# stderr on a finding.

$ErrorActionPreference = 'Continue'

$file = if ($args.Count -ge 1) { $args[0] } else { '' }
if (-not $file -or -not $file.EndsWith('.md')) { exit 0 }
if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { exit 0 }

$prefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { Join-Path $HOME '.claude' }
$clone = $null
try {
    $cfg = Get-Content -Raw -LiteralPath (Join-Path $prefix '.agentm-config.json') | ConvertFrom-Json
    $clone = $cfg.source_clones.agentm
} catch {
    exit 0
}
if (-not $clone) { exit 0 }
$check = Join-Path $clone 'scripts/vault_note_check.py'
if (-not (Test-Path -LiteralPath $check)) { exit 0 }

$py = $null
$resolver = Join-Path $prefix 'hooks/lib/resolve-python.ps1'
if (Test-Path -LiteralPath $resolver) {
    try { $py = & $resolver 2>$null | Select-Object -First 1 } catch { $py = $null }
}
if (-not $py) {
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $cmd) { exit 0 }
    $py = $cmd.Source
}

if (-not $env:AGENTMD) {
    foreach ($name in 'agentmd.exe', 'agentmd') {
        $bin = Join-Path $HOME ".local/bin/$name"
        if (Test-Path -LiteralPath $bin) { $env:AGENTMD = $bin; break }
    }
}

$env:PYTHONDONTWRITEBYTECODE = '1'
& $py $check --file $file
exit $LASTEXITCODE
