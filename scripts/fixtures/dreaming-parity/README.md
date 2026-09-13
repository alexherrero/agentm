# dreaming-parity fixture

A scratch memory root for the dreaming binary's parity fixture (filing v2 part 6). `expected.json` was
**recorded** from the Python producers with the clock pinned to 2026-09-05 — the suffix-backlog drain, the
lifecycle policy, `consolidate.py`'s recurrence targets and `calendar_rollups.py`'s reviews — before those
producers retired with the takeover on 2026-09-05. The Go tests in `daemon/internal/dreaming/parity_test.go`
reproduce it, byte for byte for the calendar reviews; `scripts/check-dreaming-parity.sh` runs them in the
battery and in CI.

The recording is the contract now. It cannot be re-recorded, so a change to a job's decisions is a deliberate
edit to `expected.json`, reviewed as such.

Edited on purpose, 2026-09-05 (PLAN-superseded-vocabulary): the copies' `after` texts now carry the contract's
shape — `lifecycle: superseded` + `superseded_by: <canonical>` on the loser, `status` untouched, no loser-side
`supersedes:`; the Go parity test reproduces it.

Edited on purpose, 2026-09-13 (agentm-vault plan 07): a calendar review's day lines link each facet note of the
day (`- 2026-08-25 — [[2026-08-25-meetings|meetings]] (2), …`) instead of the bare-date day index the design
dropped, and the reviews say they are generated from the facet notes. A day whose note arrived by a move has no
day index, so the old link dangled.
