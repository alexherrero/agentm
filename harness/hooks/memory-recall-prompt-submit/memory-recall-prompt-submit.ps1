# memory-recall-prompt-submit — inject query-relevant MemoryVault entries on prompt submit (Windows / pwsh).
# Mirrors memory-recall-prompt-submit.sh.
#
# See hook.md in this directory for full documentation.

# NOTE: no `$ErrorActionPreference = 'Stop'` — graceful-skip pattern; hook must
# never block the user prompt. Errors are caught + swallowed inline.

$RecallPy = ".claude/skills/memory/scripts/recall.py"
if (-not (Test-Path $RecallPy)) {
    # Memory skill not installed; nothing to do.
    exit 0
}

if (-not (Get-Command python3 -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue)) {
    exit 0
}

# Resolve the interpreter that runs the memory scripts. See
# memory-recall-session-start.ps1 for the rationale + bug history.
function Resolve-AgentmPython {
    $lib = Join-Path $PSScriptRoot '..' 'lib' 'resolve-python.ps1'
    if (Test-Path -LiteralPath $lib) {
        try {
            $resolved = & $lib 2>$null | Select-Object -First 1
            if ($resolved) { return "$resolved".Trim() }
        } catch {
            # Fall through to the floor below.
        }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return 'python3' }
    return 'python'
}
$Py = Resolve-AgentmPython

# Pipe stdin through. PowerShell's process redirection forwards stdin
# automatically when using the call operator without -RedirectStandardInput.
# Use $Input to forward stdin from the script's calling pipeline if any.
$Input | & $Py $RecallPy prompt-submit
exit $LASTEXITCODE
