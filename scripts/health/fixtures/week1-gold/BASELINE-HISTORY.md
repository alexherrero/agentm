# Baseline history

One paragraph per pin, newest first, because a baseline that vanishes when its
successor lands is a number nobody can audit.

**2026-08-28 — hook-parity re-pin.** `shipped-baseline.json` re-pinned at
47/64 (73.4%) after the eval learned the three things the recall hook does that
it didn't: ×2 over-fetch before filtering, the `_daemon_admissible` post-filter,
and temporal bounds. The predecessor
(`archive/shipped-baseline-20260817-pre-parity.json`, 50/64, 78.1%) counted
`dt01`/`ep10`/`ep12` as hits though the gold set marks them
`hook_reachable: false`, and carried no corpus provenance — six further
questions had silently flipped under it from corpus drift alone (2,633
retirements, 343 rewrites, in-scope halved). Full attribution table:
`scripts/health/results/goldv3/NOTES.md`, "Hook parity" entry. Baselines now
carry a corpus fingerprint, and `--compare` refuses across fingerprints unless
`--drifted-ok` says the drift is understood.
