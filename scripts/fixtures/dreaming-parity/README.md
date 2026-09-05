# dreaming-parity fixture

A scratch memory root for the dreaming binary's parity fixture (filing v2 part 6, task 4). `expected.json` is
**recorded** from the Python layer by `scripts/health/record_dreaming_parity.py` with the clock pinned to
2026-09-05; the Go tests in `daemon/internal/dreaming/parity_test.go` reproduce it, and
`scripts/test_dreaming_parity.py` fails if the Python layer drifts from the recording. Re-record only on a
deliberate change to the Python producers — a rewritten fixture during the port is a re-audit trigger.
