# How to tune the archive

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-auto-org-shelf-and-archive.md` (FRIDAY ladder feature 5, auto-organization part 1 of 3).
> **Goal:** Understand the tidying stage's floors and caps — how long a memory holds full strength before it starts to fade, how long a work artifact sits untouched before it shelves, and how to bring a shelved or archived item back — and where to change them if the schedule doesn't fit how you actually use the vault.
> **Prereqs:** None to read this page. Changing a floor means editing `harness/skills/memory/scripts/lifecycle.py` or `dream.py` directly (see below) — there's no config file for these yet.

Tidying runs automatically, inside the weekly dreaming cycle (`dream.py`'s `run_dream_and_auto_apply`) or on demand (`python3 harness/skills/memory/scripts/dream.py --vault-path <path>`). You don't have to do anything for it to work. This page is for understanding what it's doing, and for the rare case where the default schedule doesn't fit.

## Steps

1. **Know the two lanes.** A **memory** (any entry with a `kind:` frontmatter field) fades on a stepped schedule and archives after five years of silence. An **artifact** (any entry with no `kind:` at all — a loose doc, not a proper memory entry) shelves after a year untouched. Both clocks reset to zero the moment the entry is genuinely recalled again — a lint pass, an index rebuild, or a dreaming cycle touching the file never resets either clock, only a real hit does.

2. **Read the current floors and caps in code** — there's no config file yet, so the tunable constants live directly in the modules that use them:

   | Constant | Value | File |
   |---|---|---|
   | Full strength through | 182 days (~6mo) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Half strength through | 365 days (~1y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | An eighth through | 1095 days (~3y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | A sixteenth through, then floor | 1825 days (~5y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Archive preview (heads-up in the digest) | 1642.5 days (~4.5y) | `_ARCHIVE_PREVIEW_DAYS`, `dream.py` |
   | Archive threshold (the actual move) | 1825 days (5y) | `_ARCHIVE_THRESHOLD_DAYS`, `dream.py` |
   | Shelf threshold (artifacts) | 365 days (1y) | `_SHELF_THRESHOLD_DAYS`, `dream.py` |
   | Auto-apply batch cap (per cycle, all auto-apply stages combined) | 25 | `DEFAULT_AUTO_APPLY_BATCH_CAP`, `dream_confirm.py` |
   | Anomaly breaker trailing window | 8 cycles | `ANOMALY_HISTORY_WINDOW`, `dream_confirm.py` |
   | Anomaly breaker trip threshold | 3× the trailing baseline | `ANOMALY_THRESHOLD_MULTIPLIER`, `dream_confirm.py` |

   These are calibration defaults (per the design's own Technical Debt & Risks section) — they lean conservative and have no real-use data behind them yet. Edit the constant, re-run the relevant test file (`scripts/test_memory_lifecycle.py` for the stepped bands, `scripts/test_dream.py` for the archive/shelf thresholds) to confirm the change reads sane, and — if you touch the stepped-curve constants — run the retrieval eval before trusting the new numbers in live ranking (see step 4).

3. **Bring an item back.**
   - **A shelved artifact** returns on its own: touch it again (a genuine recall hit), and the next dreaming cycle proposes moving it back to its original folder. No manual step needed. Everyday search already finds a shelved item — the shelf never left `_shelf/` out of ordinary recall, only your browse eyeline.
   - **An archived memory** doesn't return automatically — that's the one asymmetry between the two lanes. Search for it with `--include-archive` (`python3 harness/skills/memory/scripts/recall.py query "<query>" --include-archive`), or read the file directly at its `_archive/` path. There's no "un-archive" move today; if you want it back in the tier's live folder, move the file yourself.
   - **Either move reverts.** Every tidying move journals through the revert log — `RevertLog(vault_path).revert(run_id, entry_id)` restores the original file and removes the moved copy. The entry ID is in the run's digest (`_dream-staging/<run_id>/digest.md`) next to the proposal that applied it.

4. **The stepped decay curve is shadow-mode only until the eval holds.** The stepped curve computes alongside the original 30-day exponential curve, but nothing wires it into live ranking yet — that's a deliberate, separate future step, not something this page's floors control. Run the comparison yourself: `python3 scripts/health/eval_v6_retrieval.py --vault-path <path> --decay-curve stepped`. It reports the same three signals (accuracy, compression, discovery-rate) the original RRF-retrieval eval does, comparing today's live exponential-decay ranking against the same ranking with the stepped curve substituted in.

## Where an entry's cold clock resets

The clock only resets on a genuine recall — `recall.py`'s `prompt_submit()` is the sole call site that resets it, by design (`lifecycle.py`'s own docstring). If you're wondering why an entry you just *read* (via a direct file open, a skill, or anything other than ordinary recall) still looks cold: that's expected. Only the recall pipeline counts as a touch.

## Verify

- `TestSteppedDecayScore` / `TestShadowModeComparison` (`scripts/test_memory_lifecycle.py`) — the stepped curve's four bands and boundaries, and that the shadow comparison never mutates the sidecar.
- `TidyingStageBandTests` / `ArtifactShelfBandTests` (`scripts/test_dream.py`) — the exact fixture bands this page's table lists, plus the recall-resets-the-clock and touched-shelved-artifact-returns cases.
- `AnomalyBreakerTests` (`scripts/test_dream_confirm.py`) — the anomaly breaker's trip threshold and its no-poisoning-the-baseline guarantee.

## Troubleshooting

- **A note I thought was long-cold hasn't archived yet.** Check `.lifecycle.json` at your vault root for its `last_access` — a stale sidecar entry from before the tidying stage shipped, or a recall hit you forgot about, resets the clock. Durable-tier entries (`lifecycle_tier: durable`), `kind: failure-incident` entries, and anything under a `decisions/` path never archive at all, regardless of age.
- **A shelved artifact didn't come back after I touched it.** Return isn't instant — it's proposed on the *next* dreaming cycle after the touch, same as every other tidying move (staged, then auto-applied). Run `/dream` by hand if you don't want to wait for the weekly schedule.
- **A whole batch of proposals didn't apply, and the digest shows "ANOMALY BREAKER TRIPPED."** The cycle proposed several times the usual tidying volume — the breaker suppressed the whole batch rather than applying an abnormal one. Every proposal is still there, staged and confirmable by hand (`dream_confirm.confirm(vault_path, run_id, index, revert_log)`) if the volume is genuinely expected (e.g. right after this feature first shipped, against a vault with years of backlog).

## See also

- [AgentM Memory System design](../designs/agentm-memory-system) — the archive/decay/prune convention this page tunes.
- [AgentM Auto-Organization design](../designs/agentm-auto-organization) — the full tidying-stage design, including the stepped-curve rationale and the automation guards.
- [Memory MCP tools reference](Memory-MCP-Tools) — the tool surface an archived or shelved entry stays reachable through (`--include-archive`, everyday search for `_shelf/`).
