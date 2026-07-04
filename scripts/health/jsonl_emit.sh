#!/usr/bin/env bash
# jsonl_emit.sh — shared JSONL check-record emitter (R1.8 Task 2). Sourced by
# every verify-*.sh; not a standalone script (no shebang execution path).
#
# Contract: the sourcing script sets HEALTH_SUITE + HEALTH_AXIS, scans its own
# "$@" for a "--jsonl-out <path>" pair into JSONL_OUT (falling back to
# $HEALTH_JSONL_OUT if the flag wasn't passed — the PLAN-r1-regression-net.md
# Locked design calls' "--jsonl-out <path> passed via ... or --jsonl-out" both
# forms), then calls emit_jsonl_check from its own pass()/fail()/skip().
#
# emit_jsonl_check <check-description> <pass: 1|0|null> [weight]
# "null" is for a SKIPPED check (e.g. a backend unavailable on this machine)
# — health_score.py excludes any pass:null record from both the numerator
# and denominator, same as a dark check, without needing dark:true (that
# flag is reserved for Task 5's designed-not-built registry; a skip here is
# "not applicable on this run", a different reason for the same exclusion).
# No-ops silently when JSONL_OUT is unset — every verify script's normal
# PASS/FAIL/SKIP table behavior is completely unaffected by this file.
emit_jsonl_check() {
  [ -n "${JSONL_OUT:-}" ] || return 0
  local check="$1" passed="$2" weight="${3:-1.0}" pass_json escaped
  case "$passed" in
    1) pass_json=true ;;
    0) pass_json=false ;;
    *) pass_json=null ;;
  esac
  escaped="$(printf '%s' "$check" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '{"suite": "%s", "axis": "%s", "check": "%s", "pass": %s, "weight": %s}\n' \
    "${HEALTH_SUITE:-unknown}" "${HEALTH_AXIS:-unknown}" "$escaped" "$pass_json" "$weight" \
    >> "$JSONL_OUT"
}

# --jsonl-out <path> scan: sourcing scripts pass "$@" through this after
# setting HEALTH_SUITE/HEALTH_AXIS. Kept out-of-band (a function, not
# executed at source time) so scripts control exactly when it runs relative
# to their own arg handling.
resolve_jsonl_out() {  # resolve_jsonl_out "$@"
  JSONL_OUT="${HEALTH_JSONL_OUT:-}"
  while [ $# -gt 0 ]; do
    case "$1" in
      --jsonl-out) JSONL_OUT="${2:-}"; shift 2 ;;
      --jsonl-out=*) JSONL_OUT="${1#--jsonl-out=}"; shift ;;
      *) shift ;;
    esac
  done
}
