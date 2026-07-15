#!/usr/bin/env bash
# enable-unattended-merge.sh — let `gh pr merge` run without a prompt in an
# unattended (no-human) agentm dispatch, by moving `Bash(gh pr merge:*)` to the
# `allow` list of your GLOBAL ~/.claude/settings.json.
#
# WHY THIS IS A SEPARATE, OPT-IN SCRIPT — never run by install.sh:
#   install.sh runs for anyone, in any project. This script edits your *global*
#   Claude Code config and loosens a security gate: it removes
#   `Bash(gh pr merge:*)` from your `ask`/`deny` lists and adds it to `allow`.
#   That's a deliberate, informed choice about your own machine, so it only
#   happens when you run it yourself. agentm's installer deliberately never
#   touches the global permission surface, and its doctor only *detects* the
#   gap (machinery_doctor.py's unattended-merge-gate check) and points here.
#
#   The mechanical reason it's a *move*, not just an append: Claude Code
#   resolves permissions deny > ask > allow, so an `ask` entry beats any
#   `allow` at any scope. Adding an allow while the rule stays in `ask` does
#   nothing — the ask entry has to go.
#
# The operator's dev-setup dotfiles (link-configs.sh) provision this
# automatically on their own machines; this script is the same change for
# anyone who clones agentm without dev-setup. See wiki/designs/agentm-autonomy.md
# (amendment log, 2026-07-15) for the full where-to-provision ruling.
#
# Idempotent: re-running when already allow-not-ask/deny is a no-op.
# Order-preserving: appends to `allow` only if absent.
#
# Usage:
#   bash scripts/enable-unattended-merge.sh [SETTINGS_JSON]
# SETTINGS_JSON defaults to ~/.claude/settings.json.
set -euo pipefail

RULE='Bash(gh pr merge:*)'
SETTINGS="${1:-$HOME/.claude/settings.json}"

command -v jq >/dev/null 2>&1 || { echo "error: jq is required" >&2; exit 1; }

if [[ ! -f "$SETTINGS" ]]; then
  echo "error: $SETTINGS not found — start Claude Code once to create it, then re-run" >&2
  exit 1
fi
if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
  echo "error: $SETTINGS is not valid JSON — fix it before re-running" >&2
  exit 1
fi

if jq -e --arg r "$RULE" '
      (.permissions.allow // [] | index($r)) != null
      and (.permissions.ask  // [] | index($r)) == null
      and (.permissions.deny // [] | index($r)) == null
    ' "$SETTINGS" >/dev/null; then
  echo "already enabled: $RULE is in allow (and not ask/deny) in $SETTINGS"
  exit 0
fi

tmp="$(mktemp)"
jq --arg r "$RULE" '
    .permissions = (.permissions // {})
  | .permissions.ask   = ((.permissions.ask   // []) - [$r])
  | .permissions.deny  = ((.permissions.deny  // []) - [$r])
  | .permissions.allow = (
      if ((.permissions.allow // []) | index($r)) then (.permissions.allow // [])
      else ((.permissions.allow // []) + [$r]) end
    )
' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
echo "enabled: moved $RULE to allow (removed from ask/deny) in $SETTINGS"
