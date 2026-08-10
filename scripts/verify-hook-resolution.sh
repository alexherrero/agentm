#!/usr/bin/env bash
# verify-hook-resolution.sh — regression guard for the hook config dual-key
# read (R1.2 / agentmEngine#0).
#
# Pre-fix, the four memory hooks' `_resolve_vault_path()` only checked a bare
# `vault_path` key in `.agentm-config.json`. The real config written by
# `agentm_config.py --vault-path` stores it under the plugin-namespaced key
# `plugins.obsidian-vault.vault_path` — so a fresh config with ONLY that key
# silently failed to resolve, and every hook no-op'd without a trace. R0's fix
# made the read dual-key: `d.get("plugins.obsidian-vault.vault_path") or
# d.get("vault_path")`. This script is the permanent regression guard.
#
# Builds a scratch HOME (`$HOME` — the hooks resolve config/scripts off
# `$HOME/.claude` and compute the transcript path off raw `$HOME`, not
# `AGENTM_INSTALL_PREFIX`) with a `.agentm-config.json` carrying ONLY the
# plugin-namespaced key (+ `source_clones.agentm` so the hooks find the
# scripts without a real skill install) and a scratch vault seeded with an
# always-load entry + a personal/reference entry. Runs all four hook scripts
# against it (env -u MEMORY_VAULT_PATH — the config-read path must be what
# resolves the vault, not a leaked env var) and asserts each one actually used
# the vault:
#   - recall session-start:  always-load entry appears on stdout
#   - recall prompt-submit:  a matching query surfaces the reference entry
#   - reflect-stop:          --route succeeds; a real record lands on stdout
#   - reflect-idle:          a stale orphan marker is renamed .start→.reflected
#     (MEMORY_IDLE_THRESHOLD_SEC=0 makes a fresh marker instantly "stale" so
#     the test doesn't need to fake mtimes)
#
# It also covers transcript-path resolution on both branches: the hooks read
# `transcript_path` off the payload (checks F + G), and fall back to the
# computed `~/.claude/projects/<cwd-slug>/<sid>.jsonl` when a host is too old
# to send it (checks A + C, whose payloads deliberately omit the field).
#
# The assertions below are always the POSITIVE expectations (vault resolves;
# each hook produces real, vault-derived output). Mirrors the
# VERIFY_MEMORY_FAULT convention in verify-memory-roundtrip.sh: fault mode
# doesn't swap in different expected values, it deliberately breaks the setup
# (strips the plugin-namespaced key from config entirely — no vault key of any
# kind) so the SAME assertions fail loudly, proving they have teeth. Run
# normally (no fault var), every assertion should pass on the fixed hooks; run
# under VERIFY_HOOK_RESOLUTION_FAULT=1, every VAULT-DEPENDENT assertion should
# fail — that's the fault-injection mode "detecting" what agentmEngine#0
# actually looked like (a config with the real vault key present in its real
# location, but nothing resolving because the reader hadn't been taught to look
# for it).
#
# Six assertions are vault-INDEPENDENT by construction and stay green under
# fault mode: the three dead-pointer sweeps in section E, the absent-transcript
# skip in section F, and the two marker-content checks in section G. They assert
# pointer hygiene — that a marker is cleared, or carries the fields it was
# handed, or that an unresolvable path is reported rather than guessed around —
# all true whether or not a vault resolves. That is correct, not a gap: naming
# it here so a reader running fault mode doesn't read those PASSes as teeth that
# fell out. (The header previously claimed every assertion fails under fault; it
# had not been true of section E since those checks landed.)
#
# Usage:   bash scripts/verify-hook-resolution.sh
#          VERIFY_HOOK_RESOLUTION_FAULT=1 bash scripts/verify-hook-resolution.sh
# Exit:    0 iff every check passes (CI / integration-test friendly); under
#          FAULT=1 every check is EXPECTED to fail (non-zero exit proves the
#          fault-injection mode has teeth — see Verification in the plan).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HOOKS="$REPO/harness/hooks"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-hook-resolution: $PY not found" >&2; exit 2; }

# R1.8 Task 2: JSONL check-record emission (health scorecard) — no-ops
# unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="verify-hook-resolution"
HEALTH_AXIS="memory persist+recall"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-160)"; fi
}
assert_exists() {
  if [ -e "$2" ]; then pass "$1"; else fail "$1" "missing path: $2"; fi
}
assert_absent() {
  if [ -e "$2" ]; then fail "$1" "did not expect path: $2"; else pass "$1"; fi
}

# ── scratch HOME + vault (isolated; auto-removed) ───────────────────────────
SCRATCH_HOME="$(mktemp -d)"
SV="$(mktemp -d)"
PROJ="$(mktemp -d)"
# reflect-idle backgrounds a detached (reparented) orchestration_idle.py job
# when MEMORY_VAULT_PATH resolves — fire-and-forget by design, so cleanup can
# race it mid-write. Best-effort, quiet, retried once.
cleanup() { rm -rf "$SCRATCH_HOME" "$SV" "$PROJ" 2>/dev/null; rm -rf "$SCRATCH_HOME" "$SV" "$PROJ" 2>/dev/null || true; }
trap cleanup EXIT
echo "verify-hook-resolution: scratch HOME=$SCRATCH_HOME vault=$SV project=$PROJ"

mkdir -p "$SCRATCH_HOME/.claude"

FAULT="${VERIFY_HOOK_RESOLUTION_FAULT:-}"

# Baseline config carries ONLY the plugin-namespaced key (the real shape
# agentm_config.py --vault-path writes) + source_clones.agentm (so the hooks
# find the memory scripts without a real skill install — keeps a fault-mode
# failure attributable to vault resolution, not to the script being missing).
# FAULT=1 strips the vault key entirely, leaving source_clones.agentm alone.
if [ "$FAULT" = "1" ]; then
  "$PY" -c "
import json
json.dump({'source_clones': {'agentm': '$REPO'}},
          open('$SCRATCH_HOME/.claude/.agentm-config.json', 'w'))
"
else
  "$PY" -c "
import json
json.dump({'plugins.obsidian-vault.vault_path': '$SV', 'source_clones': {'agentm': '$REPO'}},
          open('$SCRATCH_HOME/.claude/.agentm-config.json', 'w'))
"
fi

# ── seed the scratch vault ──────────────────────────────────────────────────
mkdir -p "$SV/personal/_always-load"
printf -- '---\nkind: convention\ntags: []\n---\nHOOK-RESOLUTION-CANARY: always-load entry loaded via the dual-key vault_path read.\n' \
  > "$SV/personal/_always-load/hook-resolution-canary.md"

mkdir -p "$SV/personal/reference"
printf -- '---\nkind: reference\ntags: [hook-resolution]\n---\nHOOK-RESOLUTION-CANARY reference entry: deploy runbook staging gate lives here.\n' \
  > "$SV/personal/reference/hook-resolution-canary-ref.md"

SESSION_ID="00000000-0000-0000-0000-000000000001"
# The real Claude Code slug: '/' and '.' both become '-', no extra prefix.
# This line used to mirror the hooks' own (wrong) formula verbatim, so it seeded
# the transcript at whatever path the hooks looked in — self-consistent, and
# therefore green while reflection was broken in production for 57 days. It now
# encodes the convention independently; scripts/test_transcript_slug.py pins the
# formula against known-good literals so both sides cannot silently drift again.
#
# Since the hooks read `transcript_path` off the payload, this slug is the
# FALLBACK path — checks A and C below deliberately omit the field so the
# fallback is what resolves them, and checks F and G cover the payload path.
CWD_SLUG="$(printf '%s' "$PROJ" | tr '/.' '--')"
mkdir -p "$SCRATCH_HOME/.claude/projects/$CWD_SLUG"
TRANSCRIPT="$SCRATCH_HOME/.claude/projects/$CWD_SLUG/$SESSION_ID.jsonl"
printf '%s\n%s\n' \
  '{"type":"user","message":{"role":"user","content":"remember the HOOK-RESOLUTION-CANARY deploy staging gate"}}' \
  '{"type":"assistant","message":{"role":"assistant","content":"noted"}}' \
  > "$TRANSCRIPT"

# A second transcript at a path the slug formula can NEVER produce — it sits
# under $PROJ rather than under $SCRATCH_HOME/.claude/projects/, so a hook that
# ignored `transcript_path` and computed instead could not find it by accident.
PAYLOAD_SESSION_ID="00000000-0000-0000-0000-000000000002"
PAYLOAD_TRANSCRIPT="$PROJ/elsewhere/payload-supplied.jsonl"
mkdir -p "$PROJ/elsewhere"
cp "$TRANSCRIPT" "$PAYLOAD_TRANSCRIPT"

run_hook() {  # run_hook <hook-script-relpath> [stdin] — stdout only
  ( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX \
      bash "$HOOKS/$1" <<<"${2:-}" 2>/dev/null )
}

run_hook_stderr() {  # run_hook_stderr <hook-script-relpath> [stdin] — stderr only
  ( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX \
      bash "$HOOKS/$1" <<<"${2:-}" 2>&1 >/dev/null )
}

# ── A. recall session-start: always-load entry on stdout ───────────────────
SS_OUT="$(run_hook memory-recall-session-start/memory-recall-session-start.sh \
  "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJ\"}")"
assert_contains "session-start: always-load entry resolved from the scratch vault" "$SS_OUT" "HOOK-RESOLUTION-CANARY"

# ── B. recall prompt-submit: matching query surfaces the reference entry ───
PS_OUT="$(run_hook memory-recall-prompt-submit/memory-recall-prompt-submit.sh \
  '{"hookEventName":"UserPromptSubmit","prompt":"what do you know about the HOOK-RESOLUTION-CANARY deploy staging gate"}')"
assert_contains "prompt-submit: reference entry resolved from the scratch vault" "$PS_OUT" "hook-resolution-canary-ref"

# ── C. reflect-stop: --route succeeds; a real record lands on stdout ───────
# No `transcript_path` on this payload — this is the computed-fallback branch,
# which stays live for hosts too old to send the field.
RS_OUT="$(run_hook memory-reflect-stop/memory-reflect-stop.sh \
  "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJ\"}")"
assert_contains "reflect-stop: --route emits a summary pass record (computed fallback)" "$RS_OUT" '"pass": "summary"'

# ── D. reflect-idle: orphan marker renamed .start→.reflected iff resolvable ─
rm -rf "$PROJ/.harness"; mkdir -p "$PROJ/.harness"
MARKER="$PROJ/.harness/session-id-$SESSION_ID.start"
cat > "$MARKER" <<EOF
session_id: $SESSION_ID
started_at: 2026-01-01T00:00:00Z
transcript: $TRANSCRIPT
EOF
( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX MEMORY_IDLE_THRESHOLD_SEC=0 \
    bash "$HOOKS/memory-reflect-idle/memory-reflect-idle.sh" >/dev/null 2>&1 )
assert_absent "reflect-idle: orphan marker consumed (.start removed)" "$MARKER"
assert_exists "reflect-idle: orphan marker reflected (.reflected written)" "${MARKER%.start}.reflected"

# ── E. reflect-idle: dead pointers are cleared, not skipped forever ─────────
# An unresolvable marker past the idle threshold has nothing left to reflect
# from. These two branches used to `continue`, which is how 200 markers piled up
# in this repo — every one unresolvable (the '--' slug bug) and every one skipped
# rather than cleared, on every session, for 57 days. A marker is a regenerable
# pointer; the transcript, where one exists, is never touched.
DEAD_GONE="$PROJ/.harness/session-id-dead-transcript.start"
cat > "$DEAD_GONE" <<EOF
session_id: dead-transcript
started_at: 2026-01-01T00:00:00Z
transcript: $PROJ/definitely-not-here.jsonl
EOF
DEAD_NOLINE="$PROJ/.harness/session-id-dead-noline.start"
printf 'session_id: dead-noline\nstarted_at: 2026-01-01T00:00:00Z\n' > "$DEAD_NOLINE"
( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX MEMORY_IDLE_THRESHOLD_SEC=0 \
    bash "$HOOKS/memory-reflect-idle/memory-reflect-idle.sh" >/dev/null 2>&1 )
assert_absent "reflect-idle: dead pointer cleared (transcript missing)" "$DEAD_GONE"
assert_absent "reflect-idle: dead pointer cleared (no transcript: line)" "$DEAD_NOLINE"
assert_absent "reflect-idle: dead pointer not resurrected as .reflected" "${DEAD_GONE%.start}.reflected"

# ── F. reflect-stop: the payload's transcript_path wins over the formula ────
# The fixture lives outside $SCRATCH_HOME/.claude/projects/ entirely, so a hook
# that computed the path instead of reading it would miss and skip. Asserted on
# the success transparency line ("...candidates from <path>"), not merely on the
# path appearing somewhere in stderr — the route-failed line names the transcript
# too, so the looser match would go green on a hook that resolved the right file
# and then failed to mine it, and would survive fault mode.
RS_PAYLOAD_ERR="$(run_hook_stderr memory-reflect-stop/memory-reflect-stop.sh \
  "{\"session_id\":\"$PAYLOAD_SESSION_ID\",\"cwd\":\"$PROJ\",\"transcript_path\":\"$PAYLOAD_TRANSCRIPT\"}")"
assert_contains "reflect-stop: mined the payload-supplied transcript_path" \
  "$RS_PAYLOAD_ERR" "candidates from $PAYLOAD_TRANSCRIPT"

# A payload path that does not exist is skipped, never silently retried against
# the computed one — the whole point is to stop guessing at a path.
RS_GHOST_ERR="$(run_hook_stderr memory-reflect-stop/memory-reflect-stop.sh \
  "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJ\",\"transcript_path\":\"$PROJ/no-such-transcript.jsonl\"}")"
assert_contains "reflect-stop: absent payload transcript_path skips (no fallback guess)" \
  "$RS_GHOST_ERR" "transcript not found: $PROJ/no-such-transcript.jsonl"

# ── G. session-start: marker records the payload transcript_path + source ───
rm -rf "$PROJ/.harness"; mkdir -p "$PROJ/.harness"
run_hook memory-recall-session-start/memory-recall-session-start.sh \
  "{\"session_id\":\"$PAYLOAD_SESSION_ID\",\"cwd\":\"$PROJ\",\"transcript_path\":\"$PAYLOAD_TRANSCRIPT\",\"source\":\"resume\"}" >/dev/null
MARKER_G="$PROJ/.harness/session-id-$PAYLOAD_SESSION_ID.start"
if [ -f "$MARKER_G" ]; then MARKER_G_BODY="$(cat "$MARKER_G")"; else MARKER_G_BODY=""; fi
assert_contains "session-start: marker carries the payload transcript_path" \
  "$MARKER_G_BODY" "transcript: $PAYLOAD_TRANSCRIPT"
assert_contains "session-start: marker records the SessionStart source" "$MARKER_G_BODY" "source: resume"

# The marker the payload wrote must be resolvable by the sweeper that reads it —
# the round trip that was broken for 57 days, now closed without a formula.
( cd "$PROJ" && HOME="$SCRATCH_HOME" env -u MEMORY_VAULT_PATH -u AGENTM_INSTALL_PREFIX MEMORY_IDLE_THRESHOLD_SEC=0 \
    bash "$HOOKS/memory-reflect-idle/memory-reflect-idle.sh" >/dev/null 2>&1 )
assert_exists "reflect-idle: payload-written marker reflects (not cleared as dead)" \
  "${MARKER_G%.start}.reflected"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-hook-resolution: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
