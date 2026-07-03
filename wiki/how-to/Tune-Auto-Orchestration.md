# How to tune auto-orchestration

> [!NOTE]
> **Goal:** Adjust the auto-orchestration toggles, thresholds, and cooldowns so the SessionStart briefing and idle-time memory chain fire on a cadence that fits how you work.
> **Prereqs:** `MEMORY_VAULT_PATH` set; harness with the auto-orchestration push-surface installed (V4 #23). See [Auto-orchestration config](Auto-Orchestration-Config) for every key this page edits.

The tunables live in `<vault>/personal/auto-orchestration-config.md`, auto-seeded with sensible defaults the first time the push-surface runs. The file is yours to edit — a re-seed never clobbers your edits. Every key sits in one `settings` fence; you change a value, save, and the next run picks it up. This page is the recipe for editing a threshold, a cooldown, or a toggle, then verifying the change with `--dry-run`.

## Steps

1. **Locate (or seed) the config.** Open `<vault>/personal/auto-orchestration-config.md`. If it doesn't exist yet, seed it with the defaults:

   ```bash
   python3 harness/skills/memory/scripts/auto_orchestration.py --vault-path "$MEMORY_VAULT_PATH" seed-config
   ```

   It prints `seeded` (wrote the file) or `kept` (one already existed — your edits are safe). The values to edit live in the fenced block:

   ````markdown
   ```settings
   enable_briefing = true
   enable_idle_chain = true
   inbox_threshold = 10
   briefing_cooldown_hours = 8
   idle_chain_cooldown_hours = 24
   ...
   ```
   ````

2. **Raise or lower a briefing threshold.** In the `settings` fence, change the threshold for a signal the briefing reports too often (or not often enough) — for example, only flag the inbox once it's larger:

   ```diff
   - inbox_threshold = 10
   + inbox_threshold = 25
   ```

   The other thresholds are `watchlist_high_threshold`, `incubator_pending_threshold`, `idea_ledger_stale_months`, plus the two nudge thresholds `promote_mention_threshold` and `stale_promotion_days`.

3. **Change a cooldown so a chain fires more or less often.** Cooldowns are in hours; a non-positive value means "always eligible". To let the idle chain run twice a day instead of once:

   ```diff
   - idle_chain_cooldown_hours = 24
   + idle_chain_cooldown_hours = 12
   ```

   The others are `briefing_cooldown_hours` and `phase_reflect_cooldown_hours`.

4. **Toggle an emission on or off.** Each `enable_*` key turns one emission off entirely. To silence the SessionStart briefing while keeping the idle chain:

   ```diff
   - enable_briefing = true
   + enable_briefing = false
   ```

   The toggles are `enable_briefing`, `enable_idle_chain`, `enable_phase_integration`, `enable_promote_suggest`, `enable_stale_promotion_nudge`. (There is no per-step toggle inside the idle chain — `enable_idle_chain` gates the whole chain.)

5. **Verify the change took effect.** Confirm the config parses to the values you expect:

   ```bash
   python3 harness/skills/memory/scripts/auto_orchestration.py --vault-path "$MEMORY_VAULT_PATH" show-config
   ```

   Then dry-run the affected chain without touching state — the idle chain prints its resolved step plan and `cooldown_ok`:

   ```bash
   python3 harness/skills/memory/scripts/orchestration_idle.py --vault-path "$MEMORY_VAULT_PATH" --dry-run
   ```

   For the briefing, run the generator directly — it prints the block only when something is over threshold and the cooldown allows:

   ```bash
   python3 harness/skills/memory/scripts/orchestration_briefing.py --vault-path "$MEMORY_VAULT_PATH"
   ```

   To see when each chain last fired, inspect the `last_fire` timestamps in `<vault>/_meta/auto-orchestration-state.json`.

## See also

- [Auto-orchestration config](Auto-Orchestration-Config) — every config key and the state-file shape.
- [Auto-orchestration — the memory push-surface](Auto-Orchestration) — what the briefing and idle chain do, and why they never nag.
- [Use auto-context in harness phases](Use-Auto-Context-In-Harness-Phases) — tuning the complementary phase-boundary pull surface (recall budgets, save mode).
