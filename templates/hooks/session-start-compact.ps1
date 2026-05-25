# SessionStart hook for agentm — fires only on matcher: compact.
# PowerShell twin of session-start-compact.sh.
#
# Compaction wipes the conversation context. Claude's compaction summary
# captures themes but loses per-file specifics that /work and /review
# need. This hook prints a re-anchor reminder; Claude Code injects the
# stdout into the post-compaction context.

$ErrorActionPreference = 'SilentlyContinue'

if (-not (Test-Path -LiteralPath (Join-Path '.harness' 'PLAN.md'))) { exit 0 }

@'
[agentm] The session was just compacted. Durable state lives on disk:

- .harness/PLAN.md       — current plan and verification criteria
- .harness/progress.md   — append-only log; the most recent entries describe the in-flight task
- .harness/features.json — feature pass/fail state

Read those three files now before continuing work. Do not infer state from the compaction summary alone — it omits per-file specifics that matter for /work and /review.
'@

exit 0
