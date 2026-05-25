#!/usr/bin/env bash
# PreCompact hook for agentm.
#
# Fires before Claude Code compacts the conversation (manual /compact or
# auto). Appends a "compaction event" marker to .harness/progress.md so
# the post-compaction session has a clear anchor point in the durable
# state file.
#
# Pure side-effect. Never blocks compaction (always exits 0).

set -euo pipefail

PROGRESS=".harness/progress.md"
[[ -f "$PROGRESS" ]] || exit 0  # not a harness project — no-op

input=$(cat)
trigger=$(echo "$input" | jq -r '.trigger // "unknown"' 2>/dev/null || echo "unknown")
custom=$(echo "$input" | jq -r '.custom_instructions // ""' 2>/dev/null || echo "")

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || branch="unknown"

{
  echo ""
  echo "## compaction event — $ts"
  echo "- trigger: $trigger"
  echo "- branch: $branch"
  [[ -n "$custom" ]] && echo "- /compact instructions: $custom"
  echo "- The session was compacted at this point. To re-anchor on the"
  echo "  in-flight task, read .harness/PLAN.md and the entries above"
  echo "  this marker. The compaction summary alone is not enough."
} >> "$PROGRESS"

exit 0
