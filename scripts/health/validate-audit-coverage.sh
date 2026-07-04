#!/usr/bin/env bash
# validate-audit-coverage.sh — proves the regression net catches what the
# mythos-readiness audit caught by hand (R1.8 Task 3).
#
# SCOPE (operator-confirmed 2026-07-03): the audit's ledger names 11 verified
# blockers total. Only 5 have any code presence in THIS repo:
#   agTrack#0, agentmEngine#0, agentmEngine#1, agentmExperience#0, voice#0
# The other 6 are out of reach for an agentm-repo verify script:
#   cricketsPluginsA#0, cricketsPluginsA#1, cricketsPluginsB#0  — crickets-repo
#     code bugs (find_capability.py, finalize_unit.py, pricing.py); no
#     presence in agentm to fault-inject.
#   roadmapMaster#0, wikiAgentm#0, wikiCrickets#0 — documentation/content
#     staleness (a vault ROADMAP file, an agentm wiki page, a crickets wiki
#     page), not runtime code defects; there is no "fault-injection mode"
#     concept for a stale sentence, and no corresponding PASS/FAIL check
#     record the health-score schema could represent.
# This script covers the 5 in-scope blockers and explicitly reports the
# other 6 as out of scope — never silently omitted.
#
# Detection mechanism differs per blocker (documented per-check below,
# not force-fit into one shape):
#   - agentmEngine#0 (hook dual-key read): VERIFY_HOOK_RESOLUTION_FAULT=1
#     makes verify-hook-resolution.sh's own assertions fail (a genuine red
#     cell) — the fix is config-triggerable, so a live fault toggle exists.
#   - agentmEngine#1 (never-demote swallow) + agentmExperience#0 (drain
#     dead): the underlying guards are now STRUCTURAL (no config toggle
#     reverts them — confirmed when these were built), so their own
#     verify-*.sh fault modes pass cleanly (proving detection works, not
#     proving red). Coverage here is the unconditional, always-run
#     assertion in each script (verify-state-routing.sh case D;
#     verify-vec-index.sh's drain-on-missing-vault check) — the permanent
#     regression guard, which WOULD go red if the fix regressed.
#   - agTrack#0 (governs-index overlap) + voice#0 (recall.py priority
#     truncation): neither has a dedicated verify-*.sh script yet. Each
#     gets a self-contained fixture here that reproduces the historical bug
#     shape via a local, read-only reproduction of the pre-fix logic
#     (never patching production code) — the same "fixture validation"
#     pattern verify-state-routing.sh / verify-reflection.sh already use
#     for their own FAULT modes.
#
# Usage:   bash scripts/health/validate-audit-coverage.sh
# Exit:    0 iff all 5 in-scope blockers have a verified detection mechanism.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); }

echo "validate-audit-coverage: 5 of 11 audit blockers are in agentm-repo scope; 6 out of scope (see header)." >&2

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

# ── agentmExperience#0: vec-index drain — the unconditional missing-vault
#    check exists (VERIFY_VEC_INDEX_FAULT=1 is this blocker's own fault mode)
VI_OUT="$(VERIFY_VEC_INDEX_FAULT=1 bash "$SCRIPTS_DIR/verify-vec-index.sh" 2>&1)"; VI_RC=$?
if [ "$VI_RC" -eq 0 ] && printf '%s' "$VI_OUT" | grep -q "drain exits non-zero when the vault has vanished"; then
  pass "agentmExperience#0: VERIFY_VEC_INDEX_FAULT=1 confirms drain fails loud on a vanished vault"
else
  fail "agentmExperience#0: VERIFY_VEC_INDEX_FAULT=1 confirms drain fails loud on a vanished vault" "rc=$VI_RC; got: $(printf '%s' "$VI_OUT" | tail -3)"
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
mkdir -p "$VOICE_VAULT/personal/_always-load"
PADDING="$(printf 'x%.0s' $(seq 1 400))"
printf -- '---\npriority: low\n---\n%s\n' "$PADDING" > "$VOICE_VAULT/personal/_always-load/aaa-low.md"
printf -- '---\npriority: high\n---\n%s\n' "$PADDING" > "$VOICE_VAULT/personal/_always-load/zzz-high.md"

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
echo "validate-audit-coverage: $PASS passed, $FAIL failed (5 in-scope blockers; 6 out of agentm-repo scope — see header)"
[ "$FAIL" -eq 0 ]
