# How to tune the archive

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-auto-org-shelf-and-archive.md` (FRIDAY ladder feature 5, auto-organization part 1 of 3).
> **Goal:** Understand what ages a memory and what shelves an artifact — the lifecycle axis's two thresholds (a year of silence sinks a memory to `dormant`; five years makes it an archive proposal you confirm, in place), the artifact shelf's one (a year untouched), the caps and the anomaly breaker — and how to bring a dormant, archived or shelved item back.
> **Prereqs:** None to read this page. Changing a floor means editing `harness/skills/memory/scripts/lifecycle.py` or `dream.py` directly (see below) — there's no config file for these yet.

Tidying runs automatically, inside the weekly dreaming cycle (`dream.py`'s `run_dream_and_auto_apply`) or on demand (`python3 harness/skills/memory/scripts/dream.py --vault-path <path>`). You don't have to do anything for it to work. This page is for understanding what it's doing, and for the rare case where the default schedule doesn't fit.

## Steps

1. **Know the two lanes.** A **memory** (any entry with a `kind:` frontmatter field) never moves. It ages on the `lifecycle:` axis (filing v2 part 6): silent past `dormant_after_days` (365) it sinks to `dormant` and ranks below its active twins; the next genuine recall lifts it back; past `archive_after_days` (1825) the weekly dream cycle stages an archive *proposal* — `lifecycle: archived` written in place once you confirm it, hidden from everyday search, still on disk. The sinking and lifting are the dreaming binary's (`agentmdream`); every move lands in `<engine state dir>/lifecycle-journal.jsonl`. An **artifact** (any entry with no `kind:` at all — a loose doc, a plan close-out) still moves: untouched for a year, the tidying stage shelves it under `_shelf/`, and a touch brings it back.

2. **Read the current floors and caps in code** — there's no config file yet, so the tunable constants live directly in the modules that use them:

   | Constant | Value | File |
   |---|---|---|
   | Full strength through | 182 days (~6mo) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Half strength through | 365 days (~1y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | An eighth through | 1095 days (~3y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | A sixteenth through, then floor | 1825 days (~5y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Dormant after (a memory sinks) | 365 days (1y) | `thresholds.dormant_after_days`, `standards/storage-rules.md` (the contract; packaged default in `daemon/internal/rules/storage-rules.default.md`) |
   | Archive proposal after (confirm-gated, in place) | 1825 days (5y) — preview in the digest from 90% of the line | `thresholds.archive_after_days`, the same contract; `PREVIEW_FRACTION`, `lifecycle_transitions.py` |
   | Demotion cap (memories sunk per pass) | 25 | `DEMOTION_BATCH_CAP`, `lifecycle_transitions.py`; `-cap` on `agentmdream run` |
   | Shelf threshold (artifacts) | 365 days (1y) | `_SHELF_THRESHOLD_DAYS`, `dream.py` |
   | Auto-apply batch cap (per cycle, all auto-apply stages combined) | 25 | `DEFAULT_AUTO_APPLY_BATCH_CAP`, `dream_confirm.py` |
   | Anomaly breaker trailing window | 8 cycles | `ANOMALY_HISTORY_WINDOW`, `dream_confirm.py` |
   | Anomaly breaker trip threshold | 3× the trailing baseline | `ANOMALY_THRESHOLD_MULTIPLIER`, `dream_confirm.py` |

   These are calibration defaults (per the design's own Technical Debt & Risks section) — they lean conservative and have no real-use data behind them yet. Edit the constant, re-run the relevant test file (`scripts/test_memory_lifecycle.py` for the stepped bands, `scripts/test_dream.py` for the archive/shelf thresholds) to confirm the change reads sane, and — if you touch the stepped-curve constants — run the retrieval eval before trusting the new numbers in live ranking (see step 4).

3. **Bring an item back.**
   - **A shelved artifact** returns on its own: touch it again (a genuine recall hit), and the next dreaming cycle proposes moving it back to its original folder. No manual step needed. Everyday search already finds a shelved item — the shelf never left `_shelf/` out of ordinary recall, only your browse eyeline.
   - **A dormant memory** comes back on its own: a genuine recall lifts it to `active` on the binary's next pass. **An archived memory** doesn't — search for it with `--include-archive` (`python3 harness/skills/memory/scripts/recall.py query "<query>" --include-archive`; `include_archived` on the MCP surface; `-include-archived` on `agentmd search`), then set `lifecycle: active` by hand (`lifecycle_transitions.py transition <rel> active --actor operator`) — the journal records who did it.
   - **A shelf move and a confirmed archive both revert.** Every tidying move and every confirmed proposal journals through the revert log — `RevertLog(vault_path).revert(run_id, entry_id)` restores the original file and removes the moved copy. The entry ID is in the run's digest (`~/.local/state/agentm/dream-runs/<run_id>/digest.md` — a run's digest and revert bundle are engine state, not vault content) next to the proposal that applied it.

4. **The stepped decay curve is shadow-mode only until the eval holds.** The stepped curve computes alongside the original 30-day exponential curve, but nothing wires it into live ranking yet — that's a deliberate, separate future step, not something this page's floors control. Run the comparison yourself: `python3 scripts/health/eval_v6_retrieval.py --vault-path <path> --decay-curve stepped`. It reports the same three signals (accuracy, compression, discovery-rate) the original RRF-retrieval eval does, comparing today's live exponential-decay ranking against the same ranking with the stepped curve substituted in.

## Where an entry's cold clock resets

The clock only resets on a genuine recall — `recall.py`'s `prompt_submit()` is the sole call site that resets it, by design (`lifecycle.py`'s own docstring). If you're wondering why an entry you just *read* (via a direct file open, a skill, or anything other than ordinary recall) still looks cold: that's expected. Only the recall pipeline counts as a touch.

## Verify

- `TestSteppedDecayScore` / `TestShadowModeComparison` (`scripts/test_memory_lifecycle.py`) — the stepped curve's four bands and boundaries, and that the shadow comparison never mutates the sidecar.
- `LifecycleStageBandTests` / `ArtifactShelfBandTests` (`scripts/test_dream.py`) and `ThePolicy` (`scripts/test_lifecycle_transitions.py`) — the exact bands this page's table lists, plus the recall-resets-the-clock and touched-shelved-artifact-returns cases.
- `AnomalyBreakerTests` (`scripts/test_dream_confirm.py`) — the anomaly breaker's trip threshold and its no-poisoning-the-baseline guarantee.

## Troubleshooting

- **A note I thought was long-cold is still `active`, or has no archive proposal.** Check `.lifecycle.json` at your memory root for its last genuine access — a recall resets the clock — and `agentmdream status` for the binary's last pass: a memory sinks to `dormant` only on a pass, and only a *dormant* memory past five years is proposed for the archive. `agentmdream run -force` prints what the next pass would do.
- **A shelved artifact didn't come back after I touched it.** Return isn't instant — it's proposed on the *next* dreaming cycle after the touch, same as every other tidying move (staged, then auto-applied). Run `/dream` by hand if you don't want to wait for the weekly schedule.
- **A whole batch of proposals didn't apply, and the digest shows "ANOMALY BREAKER TRIPPED."** The cycle proposed several times the usual tidying volume — the breaker suppressed the whole batch rather than applying an abnormal one. Every proposal is still there, staged and confirmable by hand (`dream_confirm.confirm(vault_path, run_id, index, revert_log)`) if the volume is genuinely expected (e.g. right after this feature first shipped, against a vault with years of backlog).

## See also

- [AgentM Memory System design](../designs/agentm-memory-system) — the archive/decay/prune convention this page tunes.
- [AgentM Auto-Organization design](../designs/agentm-auto-organization) — the full tidying-stage design, including the stepped-curve rationale and the automation guards.
- [Memory MCP tools reference](Memory-MCP-Tools) — the tool surface an archived or shelved entry stays reachable through (`--include-archive`, everyday search for `_shelf/`).
