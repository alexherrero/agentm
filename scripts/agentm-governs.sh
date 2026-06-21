#!/usr/bin/env bash
# agentm-governs — resolve the living design that governs a file or area.
#
# Usage: agentm-governs [--json] [--root DIR] [--include-proposed] <file-or-area>
#
# Exit codes (the contract the crickets find_governing_design.py bridge targets):
#   0  governed   — a design governs the target; its repo-relative path is
#                   printed to stdout (or the full result dict with --json)
#   1  greenfield — no design governs the target (a note goes to stderr)
#   2  usage error (no target given)
#
# Thin shim over scripts/governs_resolver.py (AG Phase 2). The agentm-side half
# of the grounding loop (design-doc §6); mirrors agentm-capability.sh.
exec python3 "$(cd "$(dirname "$0")" && pwd)/governs_resolver.py" "$@"
