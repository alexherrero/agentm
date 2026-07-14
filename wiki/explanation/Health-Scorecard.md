<!-- mode: explanation -->
# Health Scorecard

The Health Scorecard turns the verify-suite's JSONL check records into a
weighted Health Index across eight families (memory persist+recall,
plan-adherence+drift, verification honesty, capability function, memory
freshness+experience, efficiency, docs+voice health, safety/recoverability).
Family weights, the schema, and the dark-checks registry (designed-not-built
capabilities, tracked separately so they never count for or against the
index) are documented in
[`scripts/health/README.md`](https://github.com/alexherrero/agentm/blob/main/scripts/health/README.md).

## Where it lives (V8 proving Lane S, 2026-07-13)

The scorecard is no longer a page this wiki renders. Two producers exist,
and neither commits anything back to this repo:

- **The local runner** (`scripts/agentm-runner.sh`, a launchd tick on this
  machine) runs the `health-pass` job daily
  (`templates/jobs/health-pass.yaml`): the fast tier feeds
  `scripts/health/health_score.py --history --html`, which appends one row
  to the health-history ledger — the vault, when one resolves
  (`<vault>/_meta/health/history.jsonl`), else a device-local fallback for
  vault-less installs — and renders the HTML report to a fixed device path.
  `/console` and the morning brief link it.
- **`.github/workflows/health-nightly.yml`** runs both the fast and heavy
  tiers on a schedule as the clean-runner regression signal, renders the
  same HTML report, and uploads it (plus the raw JSONL records) as a build
  artifact — advisory only, never a merge gate, and it writes nothing to
  the repo or the vault.

The canonical local path is:

```
~/.cache/agentm/telemetry/scorecard.html
```

GitHub renders no clickable `file://` links on a wiki page — that's a
platform limitation, not a bug — so the block above is meant to be
copy-pasted into a browser's address bar or opened directly, not clicked
from here. The vault's own `Home.md` carries a real clickable link, since
Obsidian opens local files.

## Reading it

`python3 scripts/status.py` (or `/console`) prints the last-recorded Health
Index, the per-family breakdown, and the dark-check count from the same
ledger — on demand, no re-scoring. See
[CI gates](CI-Gates) for how the nightly tiers fit into the rest of the CI
picture.
