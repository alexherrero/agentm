#!/usr/bin/env bash
# validate-audit-coverage.sh — proves the regression net catches what the
# mythos-readiness audit caught by hand (R1.8 Task 3).
#
# SCOPE (operator-confirmed 2026-07-03; updated 2026-07-05 — PLAN-r2-ledger-
# and-dist task 7): the audit's ledger names 11 verified blockers total. Only
# 4 still have code presence in THIS repo, and this script covers exactly those:
#   agTrack#0, agentmEngine#0, agentmEngine#1, voice#0
# agentmExperience#0 (the vec-index drain being dead) was the fifth until the
# vector stack was removed — see wiki/designs/agentm-rescope-week1-experiment.md.
# The subsystem it guarded no longer exists, so there is nothing left to
# regress and no check to keep; it is retired rather than reported skipped.
# The other 6 are out of reach for an agentm-repo verify script — but 3 of
# them now have dashboard visibility from the OTHER side of the seam:
#   cricketsPluginsA#0, cricketsPluginsA#1, cricketsPluginsB#0 — crickets-repo
#     code bugs (find_capability.py, finalize_unit.py, pricing.py) that
#     already had fixes + regression tests (R0.5/R0.6/R0.7); this script
#     still can't fault-inject them (no presence in agentm), but crickets'
#     own scripts/health/run-crickets-fast-tier.sh now emits a `capability
#     function` check record per blocker (--jsonl-out wired into
#     test_find_capability.py, test_finalize_unit.py, test_token_audit.py) —
#     consumable cross-repo the same way this script's own records are.
#   roadmapMaster#0, wikiAgentm#0, wikiCrickets#0 — documentation/content
#     staleness (a vault ROADMAP file, an agentm wiki page, a crickets wiki
#     page), not runtime code defects; there is no "fault-injection mode"
#     concept for a stale sentence, and no corresponding PASS/FAIL check
#     record the health-score schema could represent. Residual out-of-scope
#     count: 3 (not 6).
# This script covers the 4 in-scope blockers and explicitly reports the
# residual 3 as out of scope — never silently omitted.
#
# Detection mechanism differs per blocker (documented per-check below,
# not force-fit into one shape):
#   - agentmEngine#0 (hook dual-key read): VERIFY_HOOK_RESOLUTION_FAULT=1
#     makes verify-hook-resolution.sh's own assertions fail (a genuine red
#     cell) — the fix is config-triggerable, so a live fault toggle exists.
#   - agentmEngine#1 (never-demote swallow): the underlying guard is now
#     STRUCTURAL (no config toggle reverts it — confirmed when it was
#     built), so its own verify-*.sh fault mode passes cleanly (proving
#     detection works, not proving red). Coverage here is the
#     unconditional, always-run assertion in that script
#     (verify-state-routing.sh case D) — the permanent regression guard,
#     which WOULD go red if the fix regressed.
#   - agTrack#0 (governs-index overlap) + voice#0 (recall.py priority
#     truncation): neither has a dedicated verify-*.sh script yet. Each
#     gets a self-contained fixture here that reproduces the historical bug
#     shape via a local, read-only reproduction of the pre-fix logic
#     (never patching production code) — the same "fixture validation"
#     pattern verify-state-routing.sh / verify-reflection.sh already use
#     for their own FAULT modes.
#
# ABLATE_GATES=1 (PLAN-r3-uplift-scoring task 1, R3.1a) — the mechanical-
# uplift baseline off-state: the same 4 in-scope blockers, but the entire
# verify/check-all battery is skipped (neither verify-hook-resolution.sh,
# verify-state-routing.sh, nor check-governs-index.py runs). Asserts all 4
# planted defects go uncaught with the battery off —
# the floor the gates lift above. Additive; never combined with the normal
# run in one invocation.
#
# Usage:   bash scripts/health/validate-audit-coverage.sh
#          ABLATE_GATES=1 bash scripts/health/validate-audit-coverage.sh
# Exit:    0 iff all 4 in-scope blockers have a verified detection mechanism
#          (or, under ABLATE_GATES=1, all 4 are confirmed uncaught).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"

# PLAN-r3-uplift-scoring task 2: JSONL check-record emission (health
# scorecard) — no-ops unless --jsonl-out <path> or $HEALTH_JSONL_OUT is set.
HEALTH_SUITE="validate-audit-coverage"
HEALTH_AXIS="capability function"
source "$HERE/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

if [ "${ABLATE_GATES:-}" = "1" ]; then
  echo "validate-audit-coverage: ABLATE_GATES=1 — verify/check-all battery skipped; asserting all 4 in-scope blockers go uncaught" >&2
  # NB: the shell-level assertion ("confirmed uncaught") succeeding is
  # reported as PASS in RESULTS/PASS below, but the JSONL record's `pass`
  # field encodes whether the underlying CAPABILITY functions — which, with
  # the battery ablated, it does not. Emitting `1` here would invert the
  # mechanical-uplift signal (score_off would read as high, not low).
  for blocker in "agentmEngine#0" "agentmEngine#1" "agTrack#0" "voice#0"; do
    RESULTS+=("  PASS  ablate: $blocker is NOT caught (verify/check-all battery skipped — no gate ran)")
    PASS=$((PASS+1))
    emit_jsonl_check "ablate: $blocker is NOT caught (verify/check-all battery skipped — no gate ran)" 0
  done
  echo
  if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
  echo
  echo "validate-audit-coverage: ablated — $PASS/4 in-scope blockers confirmed uncaught with the battery skipped"
  [ "$FAIL" -eq 0 ]
  exit $?
fi

echo "validate-audit-coverage: 4 of 11 audit blockers are in agentm-repo scope; 3 of the other 6 now have dashboard visibility via crickets' own suite; 3 residual out of scope (see header)." >&2

# ── agentmEngine#0: hook dual-key read — live fault toggle produces red ───
HR_OUT="$(VERIFY_HOOK_RESOLUTION_FAULT=1 bash "$SCRIPTS_DIR/verify-hook-resolution.sh" 2>&1)"; HR_RC=$?
if [ "$HR_RC" -ne 0 ]; then
  pass "agentmEngine#0: VERIFY_HOOK_RESOLUTION_FAULT=1 produces a red verify-hook-resolution.sh run (exit $HR_RC)"
else
  fail "agentmEngine#0: VERIFY_HOOK_RESOLUTION_FAULT=1 produces a red verify-hook-resolution.sh run" "exited 0 — expected non-zero"
fi

# ── agentmEngine#1: never-demote — the unconditional case-D guard exists ──
SR_OUT="$(bash "$SCRIPTS_DIR/verify-state-routing.sh" 2>&1)"; SR_RC=$?
if [ "$SR_RC" -eq 0 ] && printf '%s' "$SR_OUT" | grep -q "never-demote: write-state exits non-zero"; then
  pass "agentmEngine#1: verify-state-routing.sh's unconditional never-demote check (case D) is live and green"
else
  fail "agentmEngine#1: verify-state-routing.sh's unconditional never-demote check (case D) is live and green" "rc=$SR_RC; got: $(printf '%s' "$SR_OUT" | tail -3)"
fi

# ── agTrack#0: governs-index overlap — fixture reproduces + gate catches it ─
GOVERNS_FIXTURE="$(mktemp -d)"
mkdir -p "$GOVERNS_FIXTURE/wiki/designs" "$GOVERNS_FIXTURE/scripts"
cat > "$GOVERNS_FIXTURE/wiki/designs/design-a.md" <<'EOF'
---
title: design-a
status: launched
kind: design
scope: feature
area: agentm/memory
governs: [scripts/shared_module.py]
---
Design A body.
EOF
cat > "$GOVERNS_FIXTURE/wiki/designs/design-b.md" <<'EOF'
---
title: design-b
status: launched
kind: design
scope: feature
area: agentm/memory
governs: [scripts/shared_module.py]
---
Design B body — duplicates design-a's exact governs: stamp.
EOF
touch "$GOVERNS_FIXTURE/scripts/shared_module.py"
GOVERNS_OUT="$("$PY" "$SCRIPTS_DIR/check-governs-index.py" --root "$GOVERNS_FIXTURE" 2>&1)"; GOVERNS_RC=$?
rm -rf "$GOVERNS_FIXTURE"
if [ "$GOVERNS_RC" -ne 0 ] && printf '%s' "$GOVERNS_OUT" | grep -q "OVERLAP"; then
  pass "agTrack#0: a duplicate-governs-stamp fixture is detected as OVERLAP (exit $GOVERNS_RC)"
else
  fail "agTrack#0: a duplicate-governs-stamp fixture is detected as OVERLAP" "rc=$GOVERNS_RC; got: $GOVERNS_OUT"
fi

# ── voice#0: recall.py always-load truncation — priority-aware fix + a
#    read-only reproduction of the pre-fix (alphabetical + hard-break) shape
VOICE_VAULT="$(mktemp -d)"
mkdir -p "$VOICE_VAULT/memory/_always-load"
PADDING="$(printf 'x%.0s' $(seq 1 400))"
printf -- '---\npriority: low\n---\n%s\n' "$PADDING" > "$VOICE_VAULT/memory/_always-load/aaa-low.md"
printf -- '---\npriority: high\n---\n%s\n' "$PADDING" > "$VOICE_VAULT/memory/_always-load/zzz-high.md"

VOICE_CURRENT="$("$PY" -c "
import sys, pathlib; sys.path.insert(0, '$SCRIPTS_DIR/../harness/skills/memory/scripts')
import recall, io
out = io.StringIO()
recall.session_start(vault=pathlib.Path('$VOICE_VAULT'), token_budget=110, stdout=out, stderr=io.StringIO())
result = out.getvalue()
print('zzz-high-kept' if '### zzz-high' in result else 'zzz-high-dropped')
" 2>&1)"

VOICE_OLD_BUG="$("$PY" -c "
import sys, pathlib; sys.path.insert(0, '$SCRIPTS_DIR/../harness/skills/memory/scripts')
import recall
vault = pathlib.Path('$VOICE_VAULT')
always_load_dir = vault / recall._ALWAYS_LOAD_REL
candidates = sorted(always_load_dir.glob('*.md'))  # pure alphabetical — no priority re-sort (the pre-fix shape)
parsed = []
for p in candidates:
    fm, body = recall._parse_frontmatter(p.read_text())
    parsed.append((p.stem, fm, body))
blocks = [recall._format_entry_for_injection(s, fm, b) for s, fm, b in parsed]
slugs = [s for s, _, _ in parsed]
kept, tokens_used = [], 0
for block, slug in zip(blocks, slugs):
    est = recall._estimate_tokens(block)
    if tokens_used + est > 110:
        break  # pre-fix bug: hard stop, not skip-and-continue
    kept.append(slug); tokens_used += est
print('zzz-high-kept' if 'zzz-high' in kept else 'zzz-high-dropped')
" 2>&1)"
rm -rf "$VOICE_VAULT"

if [ "$VOICE_CURRENT" = "zzz-high-kept" ] && [ "$VOICE_OLD_BUG" = "zzz-high-dropped" ]; then
  pass "voice#0: current code keeps the high-priority entry; the pre-fix (alphabetical+hard-break) reproduction drops it"
else
  fail "voice#0: current code keeps the high-priority entry; the pre-fix reproduction drops it" "current=$VOICE_CURRENT old-bug-repro=$VOICE_OLD_BUG"
fi

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "validate-audit-coverage: $PASS passed, $FAIL failed (4 in-scope blockers; 3 residual out of agentm-repo scope — see header)"
[ "$FAIL" -eq 0 ]
