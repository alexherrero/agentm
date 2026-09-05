# How to use the daily calendar

> [!NOTE]
> **Status: implemented** — shipped by filing v2 part 5, "the calendar" (v9.15.0, PR #548).
> **Goal:** Record a diary line or a facet entry for today in the vault's daily register, and correct one you logged wrong on a day that's already closed.
> **Prereqs:** `Calendar/` exists at the vault root (or beside the memory root in a nested Obsidian layout) — create it once, by hand; the scripts discover the register, they never create it. A resolvable memory root to pass as `--vault`.

`Calendar/YYYY/` is the agent-maintained daily register: one note per day per facet — `meetings`, `correspondence`, `docs`, `diary` — created only on a day that had content for it, plus a generated day index over whatever exists. `diary` is the zero-bar catch-all: anything worth a line that doesn't fit a standing facet lands there. See [AgentM Filing v2 § The calendar](agentm-filing-v2#the-calendar) for the full design.

## Steps

1. **See what facets are registered.**

   ```bash
   python3 harness/skills/memory/scripts/calendar_facets.py --vault <memory-root> facets
   ```

   Prints the contract's registry, one per line — `meetings`, `correspondence`, `docs`, `diary` unless `standards/storage-rules.md` names others.

2. **Record today's diary line.**

   ```bash
   python3 harness/skills/memory/scripts/calendar_facets.py --vault <memory-root> quick --text "a diary line for today"
   ```

   Creates `Calendar/YYYY/YYYY-MM-DD-diary.md` on first use that day, or appends a new timestamped paragraph to it. Prints `created ...` or `appended ...` naming the path written, relative to the vault root. `quick` always writes to today — it takes no `--day`.

3. **Log a specific facet instead of the diary.**

   ```bash
   python3 harness/skills/memory/scripts/calendar_facets.py --vault <memory-root> append --facet meetings --text "Sync about the release."
   ```

   Same append-only behavior, under `Calendar/YYYY/YYYY-MM-DD-meetings.md`. `--day YYYY-MM-DD` names a different day; a day before today refuses (see Troubleshooting) — `append` only ever adds to a day that's still open.

4. **Check the day index.** It regenerates automatically after every append or correction, so this step is for inspection, not required:

   ```bash
   python3 harness/skills/memory/scripts/calendar_index.py --vault <memory-root> --day YYYY-MM-DD --dry-run
   ```

   Lists whichever facet notes exist for that day, each with a context phrase and its entry count, plus the day's episodic session traces and system digest when either exists. A day with nothing recorded has no index at all — `--dry-run` prints `(nothing on YYYY-MM-DD)` rather than an empty file.

5. **See this week's or this month's review.** Weekly and monthly reviews are written by the dreaming binary's `calendar` job, on its own cadence — not something you run by hand day to day. `-force` skips the binary's own gate and previews a pass right now; add `-apply` to actually write:

   ```bash
   "$HOME/.local/bin/agentmdream" run -force -apply
   ```

   Writes `Calendar/YYYY/YYYY-Www-review.md` for every closed ISO week in the trailing eight, and `Calendar/YYYY/YYYY-MM-review.md` for the running month and the one before — sparse or not, only when the text changed. Drop `-apply` to preview what a pass would do without writing anything. The Python rollups CLI (`calendar_rollups.py`) retired with the takeover (filing v2 part 6, 2026-09-05); this is how you generate a review now.

6. **Correct a day that's already closed.** A plain `append` only ever targets today or a later day you name; naming a day before today with `--day` raises `ClosedDay` rather than silently editing the past:

   ```bash
   python3 harness/skills/memory/scripts/calendar_facets.py --vault <memory-root> correct --facet meetings --day 2026-09-03 --text "The release is Thursday, not Friday."
   ```

   Writes a new note dated today — e.g. `2026-09-04-meetings-corrects-2026-09-03.md` — carrying `corrects:` and `supersedes:` back to the original, which stays byte-for-byte as it was. Both days' indexes show the correction.

## Verify

- `scripts/test_calendar_facets.py` covers append-only behavior, the closed-day refusal, and the unknown-facet refusal.
- `scripts/test_calendar_corrections.py` covers `correct()` end to end — the new note's frontmatter, the untouched original, and both days' regenerated indexes.
- `scripts/test_calendar_index.py` covers the generated index: byte-stable regeneration, the empty-day no-index case, and the episodic-traces and digest sections.

## Troubleshooting

- **`error: nothing to record: the text is empty`** — `--text` was blank or whitespace-only. Nothing is written.
- **`... is closed; the register is corrected by a new dated entry, never by an edit into the past`** — you passed `append --day` naming a day before today. Use `correct` instead (step 6).
- **`facet '...' is not registered; the register carries ...`** — the facet named isn't in the contract's registry. Adding one is an edit to `standards/storage-rules.md`, never a call-site improvisation — it happens automatically as a confirm-gated proposal once a diary label recurs on three or more distinct days in thirty (the dreaming cycle's facet-promotion stage; see [AgentM Filing v2 § The calendar](agentm-filing-v2#the-calendar)).
- **`no Calendar/ space beside <vault>: the register is discovered, never conjured`** — `Calendar/` doesn't exist yet at the vault root (or the vault root above a nested memory root). Create the directory once, by hand; nothing in this feature creates it for you.

## See also

- [AgentM Filing v2 § The calendar](agentm-filing-v2#the-calendar) — the full design: why the register is discovered rather than created, the rollup cadence, and the facet-promotion rationale.
- [Memory daemon reference](Memory-Daemon#lifecycle-sources-and-facets) — the `facets` and calendar `record_kinds` contract vocabulary.
- [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — `agentmdream`'s gate, lock, journal, and its jobs in order, including `calendar`.
- [Vault write protocol](Vault-Write-Protocol) — the lock and atomic-write primitives the calendar's own writer shares with the rest of the vault.
