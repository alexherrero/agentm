#!/usr/bin/env bash
# check-retrieval-regression.sh — the reader for the retrieval bar.
#
# This gate exists because of what it replaces. `eval_v6_retrieval.py` computes a
# promotion criterion called `merge_gate_passed`, prints it, and is run by
# nothing — not this battery, not a workflow, not a job manifest. A stepped decay
# curve has sat in shadow mode behind it for weeks. A criterion with no reader is
# not a gate; it is a comment that happens to be executable.
#
# So this one is wired, and it fails rather than reports. When the ranker
# regresses against the pinned baseline on the frozen gold set, the battery goes
# red.
#
# Three states, and the difference between the last two is the whole point:
#
#   PASS  the eval ran against the shipped daemon with a fully embedded corpus,
#         and no significant regression was found.
#   SKIP  the environment cannot produce a trustworthy measurement — no daemon,
#         no vault, a cold embedder, or stale vectors past the threshold. Says
#         which, loudly. A skip is never silent, because a gate that goes quiet
#         on the machines it cannot measure is indistinguishable from one that
#         passes.
#   FAIL  it ran, and the ranking got worse.
#
# Usage:  bash scripts/check-retrieval-regression.sh
# Exit:   0 pass or skip · 1 regression

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
BIN="${AGENTMD:-agentmd}"
BASELINE="$REPO/scripts/health/fixtures/week1-gold/shipped-baseline.json"

if ! command -v "$BIN" >/dev/null 2>&1 && [ ! -x "$BIN" ]; then
  echo "check-retrieval-regression: SKIP — $BIN is not available, so the shipped"
  echo "  ranker cannot be measured. This gate needs the daemon it is grading."
  exit 0
fi
if [ ! -f "$BASELINE" ]; then
  echo "check-retrieval-regression: SKIP — no pinned baseline at $BASELINE"
  exit 0
fi

# --drifted-ok: this gate is the standing tripwire against the live vault, so
# corpus drift is expected and printed beside the verdict rather than refused.
# Experiments comparing two code paths call the eval directly and get the
# refusal default.
out="$("$PY" "$REPO/scripts/health/eval_retrieval_shipped.py" --compare "$BASELINE" --drifted-ok 2>&1)"
rc=$?
echo "$out"

case "$rc" in
  0) echo "check-retrieval-regression: clean"; exit 0 ;;
  2) echo "check-retrieval-regression: SKIP — the environment cannot produce a"
     echo "  trustworthy measurement (see the reason above). Not a pass."
     exit 0 ;;
  3) echo "check-retrieval-regression: FAIL — the comparison was refused (no"
     echo "  provenance, or a different gold set). With --drifted-ok passed, this"
     echo "  is a configuration defect, not drift: someone must re-pin."
     exit 1 ;;
  4) echo "check-retrieval-regression: FAIL — an instrument control fired (see"
     echo "  above). The index or the fixture is broken; this is never a SKIP,"
     echo "  because a dead instrument reporting quiet is how false nulls ship."
     exit 1 ;;
  *) echo "check-retrieval-regression: FAIL — the ranker regressed against the"
     echo "  pinned baseline on the frozen gold set."
     exit 1 ;;
esac
