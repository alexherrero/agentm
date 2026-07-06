#!/usr/bin/env bash
# agentm-opinion — resolve an Opinion by name to its base+supplement composite.
#
# Usage: agentm-opinion [--json] [--root DIR] <name>
#
# Exit codes (mirrors agentm-governs.sh / agentm-capability.sh):
#   0  served / base-only — the composite is printed to stdout (or the full
#                           result dict with --json)
#   1  no-opinion / error — nothing on stdout, a note to stderr
#   2  usage error (no name given)
#
# Thin shim over scripts/opinion_resolver.py (agentm-opinion-registry.md).
exec python3 "$(cd "$(dirname "$0")" && pwd)/opinion_resolver.py" "$@"
