# vault-worktree-guard.ps1 — Windows twin of vault-worktree-guard.sh.
#
# A PreToolUse hook, registered once for the machine, on Bash, EnterWorktree,
# Agent and the background-task chip's spawn_task. The decision is made by
# vault_worktree_guard.py beside this file, the same script the bash twin runs.
# Exit 2 with the reason on stderr refuses the call. Exit 0 lets it run.
#
# The reasoning is identical to the bash twin: a payload that names neither a
# worktree nor a chip exits before any Python starts, and every error means
# "allow".

$ErrorActionPreference = 'Stop'

try {
    $payload = [Console]::In.ReadToEnd()
} catch {
    exit 0
}
if (-not $payload) { exit 0 }
# The same four shapes as the bash twin's pre-filter, chosen so that a
# session's own `.claude/worktrees/` path never matches.
if (-not ($payload -like '*worktree *' -or $payload -like '*worktree\t*' -or $payload -like '*"worktree"*' -or
          $payload -like '*"EnterWorktree"*' -or $payload -like '*spawn_task*')) { exit 0 }

$guard = Join-Path $PSScriptRoot 'vault_worktree_guard.py'
if (-not (Test-Path -LiteralPath $guard)) { exit 0 }

$python = $null
foreach ($name in @('python3', 'python')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) { exit 0 }

$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    $payload | & $python $guard
    # Only a refusal blocks. Any other non-zero status is the guard's own failure.
    if ($LASTEXITCODE -eq 2) { exit 2 }
    exit 0
} catch {
    exit 0
}
