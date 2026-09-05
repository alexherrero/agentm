#!/usr/bin/env bash
# check-dreaming-parity — the dreaming binary reproduces the recorded Python pass.
#
# Filing v2 part 6 (task 6). `scripts/fixtures/dreaming-parity/expected.json`
# was RECORDED from the Python producers by scripts/health/record_dreaming_parity.py
# with the clock pinned; two things must hold for the port to be trusted:
#   1. the Go binary's planners reproduce the recording (parity_test.go, the
#      calendar review texts byte for byte),
#   2. the Python producers still produce the recording (test_dreaming_parity.py),
#      so a drift in either layer is loud and a fixture rewrite is a decision.
# Exit 0 when both hold; non-zero otherwise. Needs Go (the daemon's toolchain).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-python3}"
fail=0
echo "check-dreaming-parity: the Go port against the recording…"
( cd "$REPO/daemon" && CGO_ENABLED=0 go test -count=1 -run 'TestParityWithTheRecordedPythonPass|TestCalendarReviewsMatchTheRecordedPythonText' ./internal/dreaming ) || fail=1
echo "check-dreaming-parity: the Python producers against the recording…"
( cd "$REPO/scripts" && AGENTM_STATE_DIR="$(mktemp -d)" "$PY" test_dreaming_parity.py ) || fail=1
if [ "$fail" -ne 0 ]; then
  echo "check-dreaming-parity: FAIL — a layer drifted from scripts/fixtures/dreaming-parity/expected.json; re-record only on purpose (record_dreaming_parity.py --write) and re-audit the port" >&2
  exit 1
fi
echo "check-dreaming-parity: PASS — both layers reproduce the recording"
