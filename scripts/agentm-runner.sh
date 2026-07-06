#!/usr/bin/env bash
# agentm-runner.sh — the runner's host-agnostic entry point (agentm-runner.md).
#
# Every trigger (Claude Desktop / Antigravity Scheduled Tasks, OS cron/launchd,
# or an on-demand call) invokes this same script; only the trigger differs.
# Runs from scripts/ as cwd so `runner.cli`'s sibling-module import of
# `vault_lock` (scripts/vault_lock.py) resolves via sys.path.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
python3 -m runner.cli "$@"
