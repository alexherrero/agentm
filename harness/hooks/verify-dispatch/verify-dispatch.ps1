# verify-dispatch.ps1 — Windows twin of verify-dispatch.sh.
#
# A PostToolUse hook, registered once for the machine. Reads the Write|Edit
# payload from stdin, pulls the edited file's path out, walks up from THAT path
# to find the project the file belongs to, and runs that project's
# `.harness/verify.ps1` (falling back to `.harness/verify.sh` when only that
# exists and bash is available).
#
# The reasoning is identical to the bash twin — see verify-dispatch.sh for why
# resolution starts at the edited file rather than the cwd, and why a project
# with no verify script is a silent success rather than a warning.

$ErrorActionPreference = 'Stop'

try {
    $payload = [Console]::In.ReadToEnd()
} catch {
    exit 0
}
if (-not $payload) { exit 0 }

try {
    $d = $payload | ConvertFrom-Json
} catch {
    exit 0
}

# Write reports the path under tool_input.file_path; some responses carry it as
# tool_response.filePath. Read both, same as the bash twin.
$filePath = $null
if ($d.PSObject.Properties.Name -contains 'tool_input' -and $d.tool_input) {
    $filePath = $d.tool_input.file_path
}
if (-not $filePath -and $d.PSObject.Properties.Name -contains 'tool_response' -and $d.tool_response) {
    $filePath = $d.tool_response.filePath
}
if (-not $filePath) { exit 0 }
if (-not (Test-Path -LiteralPath $filePath)) { exit 0 }

$dir = Split-Path -Parent (Resolve-Path -LiteralPath $filePath).Path
$homeReal = $null
if ($HOME) {
    try { $homeReal = (Resolve-Path -LiteralPath $HOME).Path } catch { $homeReal = $null }
}

while ($dir) {
    # Bounded by $HOME so a stray ~/.harness/verify script cannot capture every
    # edit made anywhere on the machine.
    if ($homeReal -and $dir -eq $homeReal) { break }

    $ps1 = Join-Path $dir '.harness/verify.ps1'
    if (Test-Path -LiteralPath $ps1) {
        & pwsh -NoProfile -File $ps1 $filePath
        exit $LASTEXITCODE
    }

    # Only .sh authored: honor it when bash is reachable, rather than silently
    # doing nothing because the operator wrote the other twin.
    $sh = Join-Path $dir '.harness/verify.sh'
    if ((Test-Path -LiteralPath $sh) -and (Get-Command bash -ErrorAction SilentlyContinue)) {
        & bash $sh $filePath
        exit $LASTEXITCODE
    }

    $parent = Split-Path -Parent $dir
    if ($parent -eq $dir) { break }
    $dir = $parent
}

exit 0
