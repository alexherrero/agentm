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

Every entry in `dark-checks.jsonl` also names its owning plan — an
`"owning_plan": "<slug>"` field. That plan's close-out is responsible for
flipping the entry to a live check (or deleting it, if the battery already
covers the capability another way) before the plan is marked done. This is
the fix for a real staleness: three entries sat marked "designed, not built"
long after all three had shipped (AA5-REENTRY-VERDICT.md §1 D4, 2026-07-08) —
naming the owning plan up front is what makes close-out catch it next time.

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

The full pipeline (R1.8 Task 2, extended by AA5 C3 + C7 — every fast-tier
suite now emits `--jsonl-out` records; confirmed end-to-end at Health Index
100.0/100 on a fully patched system):

```bash
bash scripts/health/run-fast-tier.sh | python3 scripts/health/health_score.py
```

`run-fast-tier.sh` runs every suite against a shared `--jsonl-out` scratch
file: each suite's own PASS/FAIL/SKIP table still prints to stderr
unsuppressed, only the collected JSONL records go to stdout, and a suite
exiting non-zero does not abort the batch — every suite gets a chance to
contribute records regardless of its own exit code (`check-all.sh` remains
the gate; this script only reports health).

All 8 families above now have a contributing suite (AA5 C3 lit `efficiency`
via `verify-efficiency.py`; AA5 C7 lit `verification honesty` via the new
`verify-battery-integrity.py` and `docs+voice health` by wiring
`check-wiki.py` + `check-slop.py`'s existing `--jsonl-out` support into this
script) — no family renders 0.00 with zero contributing checks. `docs+voice
health`'s `check-slop.py` check degrades to a dynamic-dark record (not a
static `dark-checks.jsonl` entry) when no crickets sibling checkout is
present, e.g. in this repo's own CI (confirmed: no crickets checkout step in
any workflow) — see `check-slop.py`'s `_emit_skip_record`.

## The designed-vs-built ledger (R2.6)

```bash
python3 scripts/health/designed_vs_built.py                          # markdown report
python3 scripts/health/designed_vs_built.py --format json            # machine-readable
python3 scripts/health/designed_vs_built.py --jsonl-out records.jsonl  # feeds the scorecard
```

Walks `wiki/designs/*.md` in both agentm and crickets (the crickets sibling
is optional — a missing checkout degrades to an agentm-only report with a
warning, never a failure) and classifies each design's governed capability
into `built` / `designed-not-built` / `wiki-tracked`, replacing the
unenumerated "103 designed-not-built items" headline with a re-derivable,
per-capability count. `status: launched` is never treated as a built signal
on its own — a `wiki-tracked` design still needs its `governs:` target to
exist on disk. `--jsonl-out` emits one `capability function`-axis check
record per ledger item (`built` items pass live; everything else is a dark
check), consumable by `health_score.py` the same way any other suite's
`--jsonl-out` is.
