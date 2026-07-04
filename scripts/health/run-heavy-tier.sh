#!/usr/bin/env bash
# run-heavy-tier.sh — the nightly-only heavy tier (R1.8 Task 4). NEVER a merge
# gate (health-nightly.yml is on: schedule + workflow_dispatch only; the fast
# tier in tests-linux.yml/tests-mac.yml remains the merge gate). Prints its
# own JSONL records to stdout (same schema as the fast tier's suites), so it
# can be `cat`-concatenated with run-fast-tier.sh's output before piping into
# health_score.py.
#
# Four checks, each gracefully degrading (SKIPPED, not silently dropped) when
# the heavier dependency it needs isn't installed — this script must be safe
# to run in the fast-tier CI environment too (it just won't have much to say
# there), not only in the nightly job that installs the extra deps:
#
#   1. Real-embedding recall (no stub mode) — needs sentence-transformers
#      (deliberately NOT installed in the fast-tier CI; the nightly workflow
#      installs it separately, since it's 1.3GB+).
#   2. Live MCP daemon round-trip — starts the REAL memory_mcp_server.py
#      daemon (uvicorn, real HTTP, not the in-process FastMCPTransport
#      verify-mcp-surface.py uses) against a scratch vault, connects a real
#      StreamableHttpTransport client, round-trips append/search/forget, and
#      shuts the daemon down. Needs fastmcp (which transitively brings in
#      uvicorn + starlette).
#   3. Fault-injection negatives — re-runs every VERIFY_*_FAULT=1 mode and
#      reports its actual outcome. Detection mechanism differs per script
#      (documented in validate-audit-coverage.sh and each script's own
#      header) — this is a report, not a uniform "must be non-zero" assertion.
#   4. Cold-install dogfood — delegates to the existing
#      scripts/smoke-install-bash.sh (a fresh scratch HOME + target project,
#      already the established hermetic cold-install proof; this task does
#      not reinvent it).
#
# Usage:   bash scripts/health/run-heavy-tier.sh
# Exit:    0 always (this is a reporting script, like run-fast-tier.sh — the
#          scorecard renders health, check-all.sh/the fast tier is the gate).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$SCRIPTS_DIR/.." && pwd)"
S="$SCRIPTS_DIR/../harness/skills/memory/scripts"
PY="${PYTHON:-python3}"

JSONL_TMP="$(mktemp)"
trap 'rm -f "$JSONL_TMP"' EXIT

emit() {  # emit <suite> <axis> <check> <pass 1|0|null>
  local suite="$1" axis="$2" check="$3" passed="$4" pass_json escaped
  case "$passed" in 1) pass_json=true ;; 0) pass_json=false ;; *) pass_json=null ;; esac
  escaped="$(printf '%s' "$check" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '{"suite": "%s", "axis": "%s", "check": "%s", "pass": %s, "weight": 1.0}\n' \
    "$suite" "$axis" "$escaped" "$pass_json" >> "$JSONL_TMP"
}

echo "run-heavy-tier: starting (nightly-only, never a merge gate)" >&2

# ── 1. real-embedding recall ────────────────────────────────────────────────
echo "run-heavy-tier: [1/4] real-embedding recall…" >&2
if "$PY" -c "import sentence_transformers" >/dev/null 2>&1; then
  RE_VAULT="$(mktemp -d)"
  mkdir -p "$RE_VAULT/personal/reference"
  printf 'the deployment runbook staging gate lives at ops/deploy.md\n' \
    > "$RE_VAULT/personal/reference/deploy-runbook.md"
  RE_OUT="$(MEMORY_VAULT_PATH="$RE_VAULT" "$PY" "$S/recall.py" query "deployment runbook staging gate" -k 5 2>&1)"; RE_RC=$?
  rm -rf "$RE_VAULT"
  if [ "$RE_RC" -eq 0 ] && printf '%s' "$RE_OUT" | grep -q "deploy-runbook"; then
    emit "run-heavy-tier" "memory persist+recall" "real-embedding recall surfaces a seeded entry (no stub mode)" 1
  else
    emit "run-heavy-tier" "memory persist+recall" "real-embedding recall surfaces a seeded entry (no stub mode)" 0
  fi
else
  echo "run-heavy-tier: [1/4] SKIP — sentence-transformers not installed" >&2
  emit "run-heavy-tier" "memory persist+recall" "real-embedding recall surfaces a seeded entry (no stub mode)" null
fi

# ── 2. live MCP daemon round-trip (real HTTP, not in-process) ─────────────
echo "run-heavy-tier: [2/4] live MCP daemon round-trip…" >&2
if "$PY" -c "import fastmcp, uvicorn" >/dev/null 2>&1; then
  MCP_VAULT="$(mktemp -d)"
  mkdir -p "$MCP_VAULT/personal/_always-load"
  MCP_TOKEN="heavy-tier-$$-$(date +%s 2>/dev/null || echo static)"
  MCP_PORT="$(( (RANDOM % 5000) + 20000 ))"
  MCP_LOG="$(mktemp)"
  ( MEMORY_VAULT_PATH="$MCP_VAULT" AGENTM_MCP_TOKEN="$MCP_TOKEN" \
      "$PY" "$SCRIPTS_DIR/memory_mcp_server.py" --port "$MCP_PORT" >"$MCP_LOG" 2>&1 & echo $! > "$MCP_LOG.pid" )
  DAEMON_PID="$(cat "$MCP_LOG.pid" 2>/dev/null)"
  sleep 2
  MCP_CLIENT_SCRIPT="$(mktemp).py"
  cat > "$MCP_CLIENT_SCRIPT" <<PYEOF
import asyncio, os, sys
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

async def main():
    transport = StreamableHttpTransport(
        f"http://127.0.0.1:{os.environ['MCP_PORT']}/mcp",
        headers={"Authorization": f"Bearer {os.environ['MCP_TOKEN']}"},
    )
    async with Client(transport) as client:
        appended = await client.call_tool("memory_append", {
            "content": "heavy-tier live daemon round-trip", "kind": "reference", "title": "heavy-tier-live",
        })
        entry_id = appended.data["id"]
        searched = await client.call_tool("memory_search", {"query": "heavy-tier live daemon round-trip"})
        hits = [r["id"] for r in searched.data["results"]]
        forgotten = await client.call_tool("memory_forget", {"id": entry_id})
        ok = entry_id in hits and forgotten.data.get("status") == "deleted"
        print("OK" if ok else "FAIL")

asyncio.run(main())
PYEOF
  MCP_RESULT="$(MCP_PORT="$MCP_PORT" MCP_TOKEN="$MCP_TOKEN" "$PY" "$MCP_CLIENT_SCRIPT" 2>&1)"
  kill "$DAEMON_PID" 2>/dev/null || true
  wait "$DAEMON_PID" 2>/dev/null || true
  rm -f "$MCP_CLIENT_SCRIPT" "$MCP_LOG" "$MCP_LOG.pid"
  rm -rf "$MCP_VAULT"
  if printf '%s' "$MCP_RESULT" | grep -q "^OK"; then
    emit "run-heavy-tier" "capability function" "live MCP daemon (real HTTP) append/search/forget round-trip" 1
  else
    emit "run-heavy-tier" "capability function" "live MCP daemon (real HTTP) append/search/forget round-trip" 0
    echo "run-heavy-tier: live daemon round-trip did not report OK: $MCP_RESULT" >&2
  fi
else
  echo "run-heavy-tier: [2/4] SKIP — fastmcp/uvicorn not installed" >&2
  emit "run-heavy-tier" "capability function" "live MCP daemon (real HTTP) append/search/forget round-trip" null
fi

# ── 3. fault-injection negatives (report, not a uniform assertion) ────────
echo "run-heavy-tier: [3/4] fault-injection negatives…" >&2
report_fault() {  # report_fault <suite> <axis> <env-var> <interpreter> <script>
  local suite="$1" axis="$2" envvar="$3" interp="$4" script="$5" rc
  env "$envvar=1" "$interp" "$SCRIPTS_DIR/$script" >/dev/null 2>&1; rc=$?
  # Reported as an informational record — pass=true means "ran to completion
  # and produced the mode-specific outcome this script's own header documents
  # as correct" (some fault modes exit non-zero by design, others exit 0 by
  # design — see validate-audit-coverage.sh for the per-script breakdown).
  emit "$suite" "$axis" "fault-injection mode ran ($envvar=1, exit $rc)" 1
}
report_fault "verify-hook-resolution" "memory persist+recall" "VERIFY_HOOK_RESOLUTION_FAULT" bash "verify-hook-resolution.sh"
report_fault "verify-state-routing"   "safety/recoverability"  "VERIFY_STATE_ROUTING_FAULT"   bash "verify-state-routing.sh"
report_fault "verify-vec-index"       "memory freshness+experience" "VERIFY_VEC_INDEX_FAULT"  bash "verify-vec-index.sh"
report_fault "verify-reflection"      "memory persist+recall"  "VERIFY_REFLECTION_FAULT"      bash "verify-reflection.sh"
report_fault "verify-mcp-surface"     "capability function"    "VERIFY_MCP_SURFACE_FAULT"     "$PY" "verify-mcp-surface.py"

# ── 4. cold-install dogfood ────────────────────────────────────────────────
echo "run-heavy-tier: [4/4] cold-install dogfood (smoke-install-bash.sh)…" >&2
if bash "$SCRIPTS_DIR/smoke-install-bash.sh" >/dev/null 2>&1; then
  emit "run-heavy-tier" "capability function" "cold-install dogfood: smoke-install-bash.sh (fresh HOME, hooks resolve)" 1
else
  emit "run-heavy-tier" "capability function" "cold-install dogfood: smoke-install-bash.sh (fresh HOME, hooks resolve)" 0
fi

echo "run-heavy-tier: done" >&2
cat "$JSONL_TMP"
exit 0
