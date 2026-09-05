#!/usr/bin/env bash
# check-dreaming-parity — the dreaming binary reproduces the recorded Python pass.
#
# Filing v2 part 6. `scripts/fixtures/dreaming-parity/expected.json` was RECORDED
# from the Python producers (with the clock pinned) before they retired with the
# takeover on 2026-09-05; the recording is the contract now — it cannot be
# re-recorded, so a change to a job's decisions is a deliberate edit to it.
# The Go planners must reproduce it (parity_test.go, the calendar review texts
# byte for byte). Exit 0 when they do. Needs Go (the daemon's toolchain).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "check-dreaming-parity: the Go port against the recording…"
if ! ( cd "$REPO/daemon" && CGO_ENABLED=0 go test -count=1 -run 'TestParityWithTheRecordedPythonPass|TestCalendarReviewsMatchTheRecordedPythonText' ./internal/dreaming ); then
  echo "check-dreaming-parity: FAIL — the binary drifted from scripts/fixtures/dreaming-parity/expected.json; a changed decision is an edit to the recording, made on purpose" >&2
  exit 1
fi
echo "check-dreaming-parity: PASS — the binary reproduces the recording"
