# scripts/health/ — the health scorecard (R1.8 dashboard v1)

Turns the verify-suite JSONL check records into a one-command health
scorecard: `bash scripts/check-all.sh && python3 scripts/health/health_score.py`.

## Schema

One JSON object per line:

```json
{"suite": "hook-resolution", "axis": "memory persist+recall", "check": "plugin-key-resolves", "pass": true, "weight": 1.0}
```

Dark checks (designed-not-built capabilities — R1.8 Task 5) use `"pass": null,
"dark": true` instead of a boolean `pass`. They render as a distinct count per
family and never reduce that family's denominator.

## Families (locked weights)

| Family (`axis` value) | Weight |
|---|---:|
| memory persist+recall | 25 |
| plan-adherence+drift | 15 |
| verification honesty | 15 |
| capability function | 15 |
| memory freshness+experience | 10 |
| efficiency | 10 |
| docs+voice health | 5 |
| safety/recoverability | 5 |

A check's `axis` field must match one of these exactly — v1 has no separate
axis→family rollup (a check's axis IS its family). Unrecognized axis names are
excluded from the Health Index and surfaced as a warning on the scorecard.

## Regression rule

Any family scoring 3+ points below its last green run, or any blocker-tier
check going red, flips the scorecard headline red. Comparisons are only valid
within the same `(fixture_pack_version, rule_pack_version)` bucket — bumping
either version starts a new baseline (`scripts/health/history.jsonl` never
edits a prior row).

## Running it

```bash
# Render a scorecard from a fixture or a verify-suite's JSONL output:
cat some-records.jsonl | python3 scripts/health/health_score.py

# JSON output instead of markdown:
cat some-records.jsonl | python3 scripts/health/health_score.py --format json

# Merge in dark checks (unbuilt capabilities):
cat some-records.jsonl | python3 scripts/health/health_score.py --dark-checks scripts/health/dark-checks.jsonl

# Append a history.jsonl row for this run:
cat some-records.jsonl | python3 scripts/health/health_score.py --history

# Determinism gate (also wired into check-all.sh): two runs at the same
# input must produce byte-identical output.
cat some-records.jsonl | python3 scripts/health/health_score.py --check-determinism
```

The full pipeline (R1.8 Task 2 — all nine `verify-*` suites now emit `--jsonl-out` records; confirmed end-to-end at Health Index 100.0/100 on a fully patched system):

```bash
bash scripts/health/run-fast-tier.sh | python3 scripts/health/health_score.py
```

`run-fast-tier.sh` runs all nine suites against a shared `--jsonl-out` scratch
file: each suite's own PASS/FAIL/SKIP table still prints to stderr
unsuppressed, only the collected JSONL records go to stdout, and a suite
exiting non-zero does not abort the batch — every suite gets a chance to
contribute records regardless of its own exit code (`check-all.sh` remains
the gate; this script only reports health).

Today this covers 5 of the 8 families above (memory persist+recall,
plan-adherence+drift, capability function, memory freshness+experience,
safety/recoverability) — verification honesty, efficiency, and docs+voice
health have no contributing suite yet.
