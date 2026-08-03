# resolve-python.ps1 — print the Python interpreter the memory hooks should run.
# Mirrors resolve-python.sh; see that file for the full rationale.
#
# THE CANONICAL RESOLVER (Windows / pwsh half). The memory hooks' .ps1 twins
# call this rather than each re-deriving a candidate list, for the same reason
# the bash half exists once: a second implementation is a second thing to drift.
#
# WHY THIS EXISTS. The vec-index backend needs
# `sqlite3.Connection.enable_load_extension`; sqlite-vec is a loadable native
# extension, so an interpreter built without it makes `vec_index._open_index()`
# return None forever, which every caller reads as the graceful "index not built
# yet" skip. The failure is silent. Apple's macOS system Python is the case that
# prompted this, but the property is not macOS-specific — any sqlite3 built
# without `--enable-loadable-sqlite-extensions` behaves the same way, so the
# Windows half probes rather than assumes.
#
# RESOLUTION ORDER.
#   1. $env:AGENTM_PYTHON        — explicit operator override, honored as given
#   2. $env:AGENT_TOOLKIT_PYTHON — back-compat alias for the same knob
#   3. first candidate whose sqlite3 build supports extension loading
#   4. `python3`, else `python` — the pre-resolver behavior, the floor
#
# An explicit override wins outright whenever it resolves to something
# runnable, and is not re-probed: an operator who names an interpreter gets it,
# and the doctor check names the override if it turns out to be incapable.
#
# Always prints exactly one line and never throws — a resolver that failed
# would turn a graceful degradation into a broken hook.

# NOTE: no `$ErrorActionPreference = 'Stop'` — this is called from graceful-skip
# hooks that must never block on it.

function Test-AgentmPythonCapable {
    param([string] $Exe)
    # Asks about the interpreter's *build*, deliberately — not whether
    # sqlite_vec is importable. `enable_load_extension` is fixed at compile
    # time; a missing sqlite_vec is one `pip install` away, so it must not veto
    # the only viable interpreter. The doctor reports the package separately.
    try {
        & $Exe -c 'import sqlite3, sys; sys.exit(0 if hasattr(sqlite3.Connection, "enable_load_extension") else 1)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# 1 + 2 — explicit override, honored as given.
foreach ($override in @($env:AGENTM_PYTHON, $env:AGENT_TOOLKIT_PYTHON)) {
    if ($override -and (Get-Command $override -ErrorAction SilentlyContinue)) {
        Write-Output $override
        exit 0
    }
}

# 3 — probe. Bare names lead so an already-capable box keeps today's behavior
# after a single probe; versioned names follow because some installers (notably
# Homebrew on the POSIX side, and per-version Windows installs) ship only those.
$candidates = @('python3', 'python')
foreach ($minor in 14, 13, 12, 11, 10, 9) {
    $candidates += "python3.$minor"
}

foreach ($c in $candidates) {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { continue }
    if (Test-AgentmPythonCapable -Exe $c) {
        Write-Output $c
        exit 0
    }
}

# 4 — floor: exactly what the hooks did before this resolver existed.
# vec_index.py's own graceful skip still applies, so this is never worse.
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    Write-Output 'python3'
} else {
    Write-Output 'python'
}
exit 0
