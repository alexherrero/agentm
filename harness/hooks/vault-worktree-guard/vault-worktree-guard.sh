#!/usr/bin/env bash
# vault-worktree-guard.sh — refuse a git worktree in the vault.
#
# A PreToolUse hook, registered once for the machine, on Bash, EnterWorktree,
# Agent and the background-task chip's spawn_task. The decision is made by
# vault_worktree_guard.py beside this file; its docstring says what is refused
# and why. Exit 2 with the reason on stderr refuses the call. Exit 0 lets it run.
#
# THE PRE-FILTER. This hook fires on every Bash call on the machine. Most of
# them have nothing to do with worktrees, so a payload that shows none of the
# four shapes below exits here, before any Python starts. The shapes are chosen
# so that a session's own path never matches: inside an agentm or crickets
# worktree the cwd always contains `.claude/worktrees/`, and a plain *worktree*
# match would start Python on every call there.
#
#   `worktree ` or `worktree\t`  the git verb, in a command line (JSON spells a tab \t)
#   `"worktree"`                 an Agent call's isolation value
#   `"EnterWorktree"`            the tool's name
#   `spawn_task`                 a background-task chip
#
# IT FAILS OPEN. No python3, or no payload, means "allow". A guard that blocked
# every Bash call on its own error would cost more than the worktree it stops.

set -uo pipefail

payload="$(cat 2>/dev/null || true)"
[[ -n "$payload" ]] || exit 0
[[ "$payload" == *"worktree "* || "$payload" == *'worktree\t'* || "$payload" == *'"worktree"'* \
   || "$payload" == *'"EnterWorktree"'* || "$payload" == *spawn_task* ]] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || exit 0
[[ -f "$here/vault_worktree_guard.py" ]] || exit 0

status=0
printf '%s' "$payload" | PYTHONDONTWRITEBYTECODE=1 python3 "$here/vault_worktree_guard.py" || status=$?
# Only a refusal blocks. Any other non-zero status is the guard's own failure.
[[ "$status" -eq 2 ]] && exit 2
exit 0
