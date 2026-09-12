# How to tune the archive

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-auto-org-shelf-and-archive.md` (FRIDAY ladder feature 5, auto-organization part 1 of 3). The dreaming binary carries the lifecycle axis; the Python cycle's tidying stage retired in agentm-vault plan 04.
> **Goal:** Understand what ages a memory — the lifecycle axis's two thresholds (a year of silence sinks a memory to `dormant`; five years names it an archive candidate, which you archive by hand, in place) and the pass's demotion cap — and how to bring a dormant or archived memory back.
> **Prereqs:** None to read this page. Changing a threshold means editing the contract (`standards/storage-rules.md`) or a constant in code (see below).

Aging runs on its own: the dreaming binary (`agentmdream`) sinks and lifts memories on its nightly pass. You don't have to do anything for it to work. This page is for understanding what it does, and for the rare case where the defaults don't fit.

## Steps

1. **Know what moves and what stays.** A **memory** (any entry with a `kind:` frontmatter field) never moves. It ages in place on the `lifecycle:` axis (filing v2 part 6). Silent past `dormant_after_days` (365), it sinks to `dormant` and ranks below its active twins; the next genuine recall lifts it back.

   Past `archive_after_days` (1825), the binary names a dormant memory as an archive candidate in its report and leaves it where it is. Archiving is yours: `lifecycle: archived`, written in place, hides the memory from everyday search and keeps it on disk. Every move lands in `<engine state dir>/lifecycle-journal.jsonl`.

   An **artifact** (any entry with no `kind:` at all, such as a loose doc or a plan close-out) stays where you put it. Anything already under a `_shelf/` folder stays there too. Everyday search still finds it, and the browse-surface meter counts it as shelved.

2. **Read the current thresholds and caps.** The two lifecycle thresholds live in the contract; the rank curve and the cap live in code:

   | Setting | Value | Where |
   |---|---|---|
   | Full strength through | 182 days (~6mo) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Half strength through | 365 days (~1y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | An eighth through | 1095 days (~3y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | A sixteenth through, then floor | 1825 days (~5y) | `_STEPPED_BANDS`, `lifecycle.py` |
   | Dormant after (a memory sinks) | 365 days (1y) | `thresholds.dormant_after_days`, `standards/storage-rules.md` (the contract; packaged default in `daemon/internal/rules/storage-rules.default.md`) |
   | Archive candidate after (named, never moved) | 1825 days (5y) — previewed from 90% of the line | `thresholds.archive_after_days`, the same contract; `PreviewFraction`, `daemon/internal/dreaming/lifecycle.go` |
   | Demotion cap (memories sunk per pass) | 200 | `DefaultDemotionCap`, `daemon/internal/dreaming/lifecycle.go`; `-cap` on `agentmdream run` |

   These are calibration defaults. They lean conservative and have no real-use data behind them yet. Edit the contract line or the constant, then re-run the tests that cover it: `scripts/test_memory_lifecycle.py` for the stepped bands, and `go test ./internal/dreaming/` from `daemon/` for the thresholds and the cap. If you touch the stepped-curve constants, run the retrieval eval before you trust the new numbers in live ranking (see step 4).

3. **Archive a memory, or bring one back.**
   - **See the candidates.** `agentmdream run -force` prints what the next pass would do, each archive candidate included, and writes no note.
   - **Archive a memory** with `python3 harness/skills/memory/scripts/lifecycle_transitions.py --vault <memory-root> set <rel> archived`. The journal records that you made the move.
   - **A dormant memory** comes back on its own: a genuine recall lifts it to `active` on the binary's next pass.
   - **An archived memory** stays archived until you act. Search for it with `--include-archive` (`python3 harness/skills/memory/scripts/recall.py query "<query>" --include-archive`; `include_archived` on the MCP surface; `-include-archived` on `agentmd search` — the same flag also brings back a superseded note, demoted, beside its successor). Then set it back with `lifecycle_transitions.py --vault <memory-root> set <rel> active`. A superseded note is a relation, not an aging state, so reviving one this way is rare; `supersession_migrate.py --apply` already does it automatically for a note whose named successor turns out not to be in the vault.
   - **A shelved artifact** comes back when you move it out of `_shelf/` yourself. The vault's git history is the undo for any move you make by hand.

4. **The stepped decay curve is shadow-mode only until the eval holds.** The stepped curve computes alongside the original 30-day exponential curve, but nothing wires it into live ranking yet — that's a deliberate, separate future step, not something this page's floors control. Run the comparison yourself: `python3 scripts/health/eval_v6_retrieval.py --vault-path <path> --decay-curve stepped`. It reports the same three signals (accuracy, compression, discovery-rate) the original RRF-retrieval eval does, comparing today's live exponential-decay ranking against the same ranking with the stepped curve substituted in.

## Where an entry's cold clock resets

The clock only resets on a genuine recall — `recall.py`'s `prompt_submit()` is the sole call site that resets it, by design (`lifecycle.py`'s own docstring). If you're wondering why an entry you just *read* (via a direct file open, a skill, or anything other than ordinary recall) still looks cold: that's expected. Only the recall pipeline counts as a touch.

## Verify

- `TestSteppedDecayScore` / `TestShadowModeComparison` (`scripts/test_memory_lifecycle.py`) — the stepped curve's four bands and boundaries, and that the shadow comparison never mutates the sidecar.
- `ThePolicy` (`scripts/test_lifecycle_transitions.py`) — a silent memory named to sink, a recalled one named to lift, an archive candidate named and never moved, the exempt states, the cap, and thresholds read from the contract. The binary's parity test (`check-dreaming-parity`) holds its lifecycle job to the recorded Python pass.

## Troubleshooting

- **A note I thought was long-cold is still `active`, or isn't named as an archive candidate.** Check `.lifecycle.json` for its last genuine access — a recall resets the clock. Since the memory-root trims (agentm-vault plan 05) it lives in `<engine state dir>`, with your memory root read as the fallback on a vault that hasn't moved yet. `agentmdream status` reports the binary's last pass. A memory sinks to `dormant` only on a pass, and only a *dormant* memory past five years is named for the archive. `agentmdream run -force` prints what the next pass would do.
- **A shelved artifact didn't come back after I touched it.** Nothing returns a shelved artifact on its own any more; the tidying stage that did so retired in agentm-vault plan 04. Move it out of `_shelf/` by hand.

## See also

- [AgentM Memory System design](../designs/agentm-memory-system) — the archive/decay/prune convention this page tunes.
- [AgentM Auto-Organization design](../designs/agentm-auto-organization) — the tidying-stage design, including the stepped-curve rationale.
- [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — the lifecycle job, its gate and its report.
- [Memory MCP tools reference](Memory-MCP-Tools) — the tool surface an archived, superseded, or shelved entry stays reachable through (`--include-archive`, everyday search for `_shelf/`).
