# Baseline history

One paragraph per pin, newest first, because a baseline that vanishes when its
successor lands is a number nobody can audit.

**2026-11-21 — the gate/report split re-pin.** Pinned on the operator's ruling of 2026-11-21. It is an exception to the standing "no re-pin until the perfection passes are done" rule, made because this is the change that keeps those passes from tripping the gate. `gold-set-v3.json` gained a `group` per question and `gate_note_paths` on ten mixed questions, so the gold-set hash moved (`1aa1-c96d-39d1` → `c562-0e74-699f`). The new pin is 3,470 documents with 3,176 embedded and 0 stale, and two runs agree on all 94 questions. **Gate: 20 of 31 (0.645). Report: 11 of 29 (0.379).** The whole set reads 31 of 60 (0.517). The predecessor is archived as `archive/shipped-baseline-20260918-pre-split.json` (42 of 63 when pinned, 2,883 documents). Against it, the last pre-split night (2026-11-21) read 0.667 → 0.533 with p 0.0225. Seven of its eleven misses expected notes the 2026-11-03 content review moved into `completed/`, and one expected Shrimpi notes walled on 2026-11-20. The same comparison read over the split was gate 23 → 20 of 31 (p 0.375) and report 18 → 11 of 29. The attribution is in [REPIN-QUEUE.md](REPIN-QUEUE.md) under the 2026-11-21 entry.

**Not a pin: the re-pin queue (2026-10-02, task 182).** No re-pin until the vault series closes and several perfection passes run (the operator's standing rule). Every question the gate misses against the 2026-09-19 baseline is named with its cause and evidence in [REPIN-QUEUE.md](REPIN-QUEUE.md), so the next re-pin starts from evidence rather than a fresh investigation.

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

**2026-08-28 — hard-negatives re-pin (same day, second pin).** The gold set
grew ten near-miss negatives (`ngh01`–`ngh10`, RULE-hard-negatives.md), so the
gold-set hash moved and the baseline re-pinned under provenance rules. Scored
questions unchanged at 64 / 47 hits; new lines: easy negatives 20 with 0 FP
(structurally quiet — empty banned lists, documented), hard negatives 10 with
10 serving their banned note, exactly as pre-registered. The canary from task 3
also sits in the corpus now (documents 7,926 → 7,928 across the two pins).
