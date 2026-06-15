#!/usr/bin/env bash
# agentm-capability — probe whether a plugin capability is available.
#
# Usage: agentm-capability <capability> [<version-range>]
#
# Exit codes:
#   0  capability available (provider installed + version satisfied)
#   1  capability unavailable (no provider / not installed / version mismatch)
#   2  usage error (wrong argument count)
#
# Thin shim over scripts/capability_resolver.py (V5-8). Call sites that
# previously used capability_probe.py (slug-keyed) switch here (capability-
# keyed) as part of the crickets cutover plan (LC-5).
exec python3 "$(cd "$(dirname "$0")" && pwd)/capability_resolver.py" "$@"
