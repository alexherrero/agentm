# How to run the card backfill

> [!NOTE]
> **Goal:** Bring every surviving note in the class directories to the card's shape without a model call (agentm-vault § The card, agentm-vault plan 06).
> **Prereqs:**
> 
> - The card backfill's code has merged to `main`.
> - You have shell access to the machine the vault and the daemon run on.
> - The configured vault path resolves to the vault you are backfilling (or `MEMORY_ROOT` names its memory root for this shell).
> - You run this outside the nightly `02:00`–`06:00` enrichment window.

`scripts/migrate/card_backfill.py` brings every note to the card's shape:

- It stamps deterministic facts (`trust` from `source`, `filing_confidence` from `confidence` against the contract's floor, `lifecycle`/`updated`/`source` where missing).
- It folds `captured` into `created`.
- It drops retired fields and empty tag lists.
- It renames counter slugs.
- It reorders every note's frontmatter to the card's order.

The script makes these changes without asking a model anything. It never writes `enriched_by` or `enriched_at`. It names what it stamped in each note's own `backfilled:` list. The script runs as a dry run by default. The applied run refuses if the vault no longer matches the dry run.

## Steps

1. **Quiesce the runner and the daemon.** Both write into the vault outside the nightly window. A write mid-backfill races the script:

   ```bash
   launchctl bootout gui/$(id -u)/com.agentm.runner
   launchctl bootout gui/$(id -u)/com.agentm.daemon
   ```

2. **Record what the night owes before you change anything.** The backfill rewrites every card's frontmatter, and step 5's ledger rebuild then records every stamped card as current against its rewritten file, including the cards the night still owes. The pending list you save here is what step 5 owes again:

   ```bash
   agentmd enrich -dry-run
   agentmd ledger --pending --limit 0 --json > /tmp/pending-before.json
   ```

3. **Dry-run the backfill.** Nothing on disk changes yet:

   ```bash
   python3 scripts/migrate/card_backfill.py
   ```

   The dry run prints:

   - A count per stamp, correction, and retirement.
   - The renames it plans to make.
   - The links it plans to rewrite. It only rewrites links under `Agent/`, `Calendar/`, and `Projects/agentm/`. It lists a link elsewhere as `not written`.
   - The standing enrichment refusals it plans to re-key.
   - The sidecar (`.heat.json`/`.lifecycle.json`) keys it plans to move.
   - The empty class-directory-internal directories it plans to remove.

   It records the plan as JSON under the engine state directory (`card-backfill/plan-<run-id>.json`). It prints the exact `--apply` command to run next. Read the `finding:` lines for information. They name directories the script skips or notes with no frontmatter block. Findings do not block the apply.

4. **Apply it**, using the exact command the dry run printed. The command requires both the plan file and the file count. The apply refuses if the vault has moved since the dry run. It also refuses if your count mismatches the plan:

   ```bash
   python3 scripts/migrate/card_backfill.py --apply --plan <PLAN> --confirm-count <N>
   ```

   The script writes through the revert log. It reads the vault back to build its journal. The journal's counts record what actually landed. It writes `memory/.card-backfill-complete` only when the journal matches the dry run exactly. An exact match means the script completes. A mismatch causes the script to exit 1. It leaves the marker off on a mismatch. A missing marker keeps the card gates in their pre-backfill, report-only state.

5. **Rebuild the ledger, then owe again every card the night owed before:**

   ```bash
   agentmd ledger --rebuild
   agentmd ledger --forget <target>   # once per target in step 2's pending list,
                                       # under its new path when the backfill renamed it
   ```

   The fingerprint the night skips on covers the whole file, so the backfill breaks every card's match. The rebuild restores it: it reads each card's own `enriched_by` stamp and records the card as done against the file as it now stands. That also marks as current the cards step 2 listed as pending, the ones owed a light pass or a retry. Forgetting each of those targets drops the recovered row, so the night owes them again. Forgetting a target with no row is harmless. The new paths are in the plan's `renames` map.

6. **Bring the daemon back:**

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.daemon.plist
   launchctl kickstart -k gui/$(id -u)/com.agentm.daemon
   ```

   Confirm the daemon with `agentmd status`. You cannot trust `launchctl list` alone (see [Memory daemon reference](Memory-Daemon)). Leave the runner stopped until step 9: its hourly jobs write to cards and would move the count step 8 reads.

7. **Reindex the embeddings:**

   ```bash
   agentmd embed
   ```

   Every renamed note is a new path for the dense arm. Skipping this step reads as a retrieval regression.

8. **Re-measure and confirm nothing the night owes moved:**

   ```bash
   agentmd enrich -dry-run
   ```

   The deep, light and unchanged counts must match what step 2 recorded. A mismatch means step 5 missed a target: compare `agentmd ledger --pending --limit 0 --json` with step 2's list.

9. **Bring the runner back:**

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.runner.plist
   launchctl kickstart -k gui/$(id -u)/com.agentm.runner
   ```

10. **Run the full gate battery:**

    ```bash
    bash scripts/check-all.sh
    ```

## Verify

- The three card gates (`python3 scripts/check-card-shape.py`, `python3 scripts/check-class-directories.py`, and `python3 scripts/check-no-empty-tags.py`) each report `clean` rather than `pre-backfill` (see [CI gates reference](CI-Gates)).
- `memory/.card-backfill-complete` exists at the memory root.
- The `agentmd enrich -dry-run` owed deep and light counts match what you recorded in step 2.
- `bash scripts/check-all.sh` passes locally.

## Troubleshooting

- **The apply refuses with "the vault is not what the dry run read."** Something wrote to the vault between the dry run and the apply. Re-run the dry run (step 3) and apply the fresh plan.
- **The apply refuses on a file-count mismatch.** You typed the wrong `--confirm-count`, or the vault moved. Re-run the dry run. Copy its printed command verbatim.
- **The journal doesn't match the dry run, and the marker is missing.** The card gates stay in their pre-backfill, report-only state. Nothing enforces yet. Read the journal at `<engine state dir>/card-backfill/journal-<run-id>.json` to find which notes or links failed to land. Fix the underlying issue. Re-run the dry run and apply.
- **You need to undo an applied run.** Run `python3 scripts/migrate/card_backfill.py --revert <RUN_ID>`. This restores every file that run wrote. It also removes `memory/.card-backfill-complete`.
- **`agentmd enrich -dry-run`'s owed count moved after the backfill.** Step 5 missed a target, or something wrote to a card after step 1. Compare today's pending list with step 2's. Forget each target that was pending then and reads current now, under its new path when the plan's `renames` map renamed it.

## Related

- [scripts/migrate/card_backfill.py](https://github.com/alexherrero/agentm/blob/main/scripts/migrate/card_backfill.py) — the migration this page runs.
- [CI gates reference § check-card-shape, check-class-directories, check-no-empty-tags](CI-Gates) — the gates this backfill brings from report-only to enforcing.
- [Vault lint checks reference](Vault-Lint-Checks) — the `field-order` and `date-format` checks that read the same card order.
- [Memory daemon reference](Memory-Daemon) — installing and reloading `agentmd`, and why `launchctl list` alone isn't proof of health.
- [Run the memory-root trims migration](Run-The-Memory-Root-Trims-Migration) — the sibling migration this page's shape follows.
- [AgentM Vault design](../designs/agentm-vault) — the governing design for the card's shape this backfill brings the corpus to.
