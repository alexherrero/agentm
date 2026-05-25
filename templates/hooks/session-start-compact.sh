#!/usr/bin/env bash
# SessionStart hook for agentm — fires only on matcher: compact.
#
# Compaction wipes the conversation context. Claude's compaction summary
# captures themes but loses per-file specifics that /work and /review
# need. This hook prints a re-anchor reminder; Claude Code injects the
# stdout into the post-compaction context.

set -euo pipefail

[[ -f .harness/PLAN.md ]] || exit 0  # not a harness project

cat <<'EOF'
[agentm] The session was just compacted. Durable state lives on disk:

- .harness/PLAN.md       — current plan and verification criteria
- .harness/progress.md   — append-only log; the most recent entries describe the in-flight task
- .harness/features.json — feature pass/fail state

Read those three files now before continuing work. Do not infer state from the compaction summary alone — it omits per-file specifics that matter for /work and /review.
EOF

exit 0
