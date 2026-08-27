# compaction-reanchor.ps1 — Windows twin of compaction-reanchor.sh.
#
# A SessionStart hook with matcher `compact`, so it fires only on the session
# that resumes from a compaction. Its stdout is injected into the
# post-compaction context.
#
# Short on purpose: harness-context-session-start fires on the same event with
# matcher `.*` and already prints where the plan and progress log live.
# Repeating that here would print it twice on exactly the sessions already short
# on context. What that hook cannot say — because it fires on every start — is
# that this session lost its conversation.

$ErrorActionPreference = 'Continue'   # never block session boot

try { $payload = [Console]::In.ReadToEnd() } catch { exit 0 }

$py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { exit 0 }

# DC-6: the event's cwd, not $PWD.
$eventCwd = $null
if ($payload) {
    try {
        $d = $payload | ConvertFrom-Json
        if ($d.cwd) { $eventCwd = $d.cwd }
    } catch { }
}
if (-not $eventCwd) { $eventCwd = (Get-Location).Path }
if (-not (Test-Path -LiteralPath $eventCwd -PathType Container)) { exit 0 }

# Resolve harness_memory.py: recorded agentm source clone -> fallback.
$resolver = $null
$cfg = Join-Path $HOME '.claude/.agentm-config.json'
if (Test-Path -LiteralPath $cfg) {
    try {
        $c = Get-Content -Raw -LiteralPath $cfg | ConvertFrom-Json
        $clone = $c.source_clones.agentm
        if ($clone) {
            $cand = Join-Path $clone 'scripts/harness_memory.py'
            if (Test-Path -LiteralPath $cand) { $resolver = $cand }
        }
    } catch { }
}
if (-not $resolver) {
    $cand = Join-Path $HOME 'Antigravity/agentm/scripts/harness_memory.py'
    if (Test-Path -LiteralPath $cand) { $resolver = $cand }
}
if (-not $resolver) { exit 0 }

# resolve-active-plan answers with PATHS, not with existence: it will happily
# name <dir>/.harness/PLAN.md for a directory that has no harness at all. Both
# files must be checked on disk before this hook says anything, or it lectures
# every compacted session on the machine about a plan that does not exist.
$pair = & $py.Source $resolver 'resolve-active-plan' '--project-root' $eventCwd 2>$null
if (-not $pair) { exit 0 }

$planPath = ($pair -split "`t")[0]
$progressPath = ($pair -split "`t")[1]
if (-not $planPath -or -not (Test-Path -LiteralPath $planPath -PathType Leaf)) { exit 0 }
if (-not $progressPath -or -not (Test-Path -LiteralPath $progressPath -PathType Leaf)) { exit 0 }
$progressName = Split-Path -Leaf $progressPath

@"
[agentm] This session resumed from a **compaction** — the previous conversation
was discarded, not paused.

Read the durable state before continuing. The compaction summary preserves
themes and loses specifics: which files were mid-edit, which assertion was
failing, which decision was already settled and should not be reopened.

Look for the most recent ``## compaction event`` marker in $progressName —
everything above it was written by the session whose context is now gone.

(The session-start context block printed alongside this one names where the
plan and progress log actually live.)
"@

exit 0
