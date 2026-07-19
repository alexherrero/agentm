#!/usr/bin/env bash
# verify-auto-org-meters.sh — end-to-end regression guard for auto-
# organization's two vault-health meters (PLAN-auto-org-dedup-and-lint.md,
# task 8) and the health-scorecard's live signal for the `memory
# freshness+experience` axis's meters row.
#
# The plan's own acceptance test, verbatim: "browsing agentm's memory shows
# live, current notes, and aged material sits in the archive, still there
# on request." Until this script existed, neither meter had any dashboard
# presence — organic connectivity only ever landed in the dream digest
# (auto-org part 2), and the three-state browse-surface count didn't exist
# anywhere. Unit-level coverage of each meter's own logic already lives in
# scripts/test_dream.py (ConnectivityMeterTests, BrowseSurfaceCountsTests) —
# this script proves the two meters actually land through `run_dream()`
# against a real fixture cycle, the same way verify-dreaming.sh proves the
# dreaming pipeline's own wiring rather than re-testing every branch.
#
# Checks:
#   A. a fixture cycle with a linked pair + an unlinked note reports the
#      expected organic-connectivity ratio in corpus_stats
#   B. the same cycle reports the correct browse-surface counts for a live
#      note, a shelved artifact, and an archived memory (one of each)
#   C. the archived note's content is still readable on disk after the
#      cycle — never deleted, satisfying the acceptance test's own words
#   D. both meters render as lines in the digest, not just corpus_stats
#
# Usage:   bash scripts/verify-auto-org-meters.sh
# Exit:    0 iff every check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/harness/skills/memory/scripts"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "verify-auto-org-meters: $PY not found" >&2; exit 2; }

HEALTH_SUITE="verify-auto-org-meters"
HEALTH_AXIS="memory freshness+experience"
source "$HERE/health/jsonl_emit.sh"
resolve_jsonl_out "$@"

PASS=0; FAIL=0
RESULTS=()
pass() { RESULTS+=("  PASS  $1"); PASS=$((PASS+1)); emit_jsonl_check "$1" 1; }
fail() { RESULTS+=("  FAIL  $1"$'\n'"          ↳ $2"); FAIL=$((FAIL+1)); emit_jsonl_check "$1" 0; }

assert_contains() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"
  else fail "$1" "expected substring: '$3'  |  got: $(printf '%s' "$2" | tr '\n' '~' | cut -c1-200)"; fi
}
assert_eq() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "want '$3'  got '$2'"; fi
}

# ── scratch vault, seeded with all three browse states + a linked pair ─────
V="$(mktemp -d)"
cleanup() { rm -rf "$V"; }
trap cleanup EXIT

mkdir -p "$V/personal/reference/_shelf" "$V/personal/reference/_archive"
printf '%s\n' "---" "slug: linked-a" "---" "" "see [[linked-b]] for detail" > "$V/personal/reference/linked-a.md"
printf '%s\n' "---" "slug: linked-b" "---" "" "see [[linked-a]] for detail" > "$V/personal/reference/linked-b.md"
printf '%s\n' "---" "slug: unlinked" "---" "" "no links here" > "$V/personal/reference/unlinked.md"
printf '%s\n' "---" "slug: shelved" "---" "" "body" > "$V/personal/reference/_shelf/shelved.md"
ARCHIVE_CONTENT="---\nslug: archived\n---\n\noriginal archived content\n"
printf '%b' "$ARCHIVE_CONTENT" > "$V/personal/reference/_archive/archived.md"

RUN_OUT="$("$PY" -c "
import sys, json
sys.path.insert(0, '$S')
import dream
digest = dream.run_dream('$V', run_id='verify-meters-run')
print(json.dumps({
    'organically_linked_count': digest.corpus_stats.get('organically_linked_count'),
    'organic_connectivity': digest.corpus_stats.get('organic_connectivity'),
    'browse_live_count': digest.corpus_stats.get('browse_live_count'),
    'browse_shelved_count': digest.corpus_stats.get('browse_shelved_count'),
    'browse_archived_count': digest.corpus_stats.get('browse_archived_count'),
    'digest_text': digest.digest_path.read_text(encoding='utf-8'),
}))
" 2>&1)"

ORGANIC="$(printf '%s' "$RUN_OUT" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('organically_linked_count'))
except Exception: print('ERR')" 2>/dev/null)"
assert_eq "A. organic-connectivity count: exactly the linked pair (2 of 3 entries)" "$ORGANIC" "2"

LIVE="$(printf '%s' "$RUN_OUT" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('browse_live_count'))
except Exception: print('ERR')" 2>/dev/null)"
assert_eq "B. browse-surface: live count (3 non-shelved entries)" "$LIVE" "3"

SHELVED="$(printf '%s' "$RUN_OUT" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('browse_shelved_count'))
except Exception: print('ERR')" 2>/dev/null)"
assert_eq "B. browse-surface: shelved count" "$SHELVED" "1"

ARCHIVED="$(printf '%s' "$RUN_OUT" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('browse_archived_count'))
except Exception: print('ERR')" 2>/dev/null)"
assert_eq "B. browse-surface: archived count" "$ARCHIVED" "1"

# ── C. the acceptance test's own words: archived material is still there ───
POST_ARCHIVE="$(cat "$V/personal/reference/_archive/archived.md")"
assert_contains "C. archived note's content is still readable on request (never deleted)" "$POST_ARCHIVE" "original archived content"

# ── D. both meters render in the digest, not just corpus_stats ─────────────
DIGEST_TEXT="$(printf '%s' "$RUN_OUT" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin).get('digest_text', ''))
except Exception: print('')" 2>/dev/null)"
assert_contains "D. digest renders a Connectivity line" "$DIGEST_TEXT" "Connectivity:"
assert_contains "D. digest renders a Browse surface line" "$DIGEST_TEXT" "Browse surface:"

# ── report ──────────────────────────────────────────────────────────────────
echo
if [ ${#RESULTS[@]} -gt 0 ]; then printf '%s\n' "${RESULTS[@]}"; fi
echo
echo "verify-auto-org-meters: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
