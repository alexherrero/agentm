# How to run the root casing migration

> [!NOTE]
> **Goal:** Rename the four vault root spaces lowercase — `Agent` to `agent`, `Calendar` to `calendar`, `Personal` to `personal`, `Projects` to `projects`, beside `standards/` — in one quiesce, after the code that spells them lowercase has merged and before it is deployed to this machine.
> **Prereqs:**
>
> - The crickets plugins are updated first, so their project-space resolvers already accept both spellings: `claude plugin update` for `development-lifecycle`, `design`, `wiki` and `research`, before the data run rather than after.
> - The agentm PR is merged to `main`, but your clone is **not yet fast-forwarded** — data before code, in one quiesce. Step 8 below does the fast-forward and rebuild.
> - You have shell access to the machine the vault and the daemon run on.
> - You run this outside the nightly `02:00`–`06:00` window.
> - Obsidian is quit — your own step; verify with `pgrep -x Obsidian`.
> - Google Drive is running, so it sees each rename as it happens.

The script `scripts/migrate/root_casing.py` renames the four root spaces on disk. It matches the code that already spells them lowercase. See [CI gates reference § check-no-title-case-roots](CI-Gates) for details. Each root moves through a temporary dot-prefixed name. The command runs `git mv <Root> .<root>-tmp && git mv .<root>-tmp <root>`. A plain case-only rename on a case-insensitive disk would register as no change at all — the temporary name is what makes it register as two real renames. A pause between roots gives Drive time to see two renames instead of a delete and a create. The script runs as a dry run first. You then `--apply` it against a confirmed count. You run `--finish` after the config and the rebuild catch up. You can undo an applied run with `--revert RUN_ID`.

## Steps

1. **Quiesce the runner and the daemon.** Both write into the vault. A write while a root sits at its temporary name lands in the wrong place.

   ```bash
   launchctl bootout gui/$(id -u)/com.agentm.runner
   launchctl bootout gui/$(id -u)/com.agentm.daemon
   ```

   Confirm no reflect hook is still finishing a write:

   ```bash
   pgrep -f memory-reflect
   ```

   The `--apply` flag checks all three. It refuses if one is still live. A rename already in progress is not where you want to find that out.

2. **Record what the night owes.** Run the link check before anything moves.

   ```bash
   agentmd enrich -dry-run
   agentmd ledger --pending --limit 0 --json > /tmp/pending-before.json
   MEMORY_ROOT=<memory-root> python3 harness/skills/memory/scripts/vault_lint.py --format json > /tmp/lint-before.json
   ```

   Step 9 compares against the ledger snapshot. Step 12 compares against the lint snapshot.

3. **Dry-run the migration.** Pass `--vault` explicitly.

   ```bash
   python3 scripts/migrate/root_casing.py --vault <vault>
   ```

   Pass `--vault` here even though sibling migrations resolve it automatically. Between the rename in step 5 and the config-key update in step 7, the vault-path/memory-root resolver's own suffix match against `memory_root` would momentarily disagree with the disk if left to auto-resolve. The dry run only reads data. It lists each root's state (`pending`, `done`, or `absent`). It lists the tracked file count. It prints a manifest. The manifest shows one line per rename with its two-step command and the reverse command. It records the plan under `<engine state dir>/root-casing/plan-<run_id>.json`.

4. **Paste the printed manifest into the plan's progress file.** Do this before anything moves. It records what is about to move. It provides the exact command that puts each root back without using `--revert`.

5. **Apply the migration.** Use the exact command and count the dry run printed. Keep Drive running.

   ```bash
   python3 scripts/migrate/root_casing.py --vault <vault> --apply --plan <PLAN> --confirm-count <N>
   ```

   It refuses, and writes nothing, if the vault's root listing no longer matches what the dry run read. It refuses if your count doesn't match the plan's count. It refuses if anything is staged in the vault's index. Step 6's hand commit must stay restricted to the renames. It refuses if a writer is still live. On success, it reports the tracked count before and after each rename. It prints `journal matches the plan: yes`. Read the vault root listing it prints. A returned Title Case directory means stop. A Drive-style duplicate such as `agent (1)` means stop. Read Troubleshooting before doing anything else.

6. **Commit the renames by hand.** Commit the settings separately. The two-step `git mv` already staged the renames. The `--apply` flag rewrote the settings files, but they are not staged.

   ```bash
   git -C <vault> commit -m "root casing: the four roots renamed"
   git -C <vault> commit -am "root casing: settings repointed to the new names"
   ```

7. **Set the config keys through the tool.** Never edit them by hand. The same config file also holds the mail door's SMTP credential.

   ```bash
   python3 scripts/agentm_config.py --memory-root agent
   python3 scripts/agentm_config.py --set-space memory agent/memory
   python3 scripts/agentm_config.py --set-space projects projects
   ```

   Point the `MEMORY_VAULT_PATH` export at the new name. The name must end in `/agent`. Update it in agentm's own `.harness/project.json`. Update it in crickets' file if it has one. Update it in the vault twin `<vault>/projects/agentm/_harness/project.json`. Confirm the two roots agree.

   ```bash
   python3 scripts/check-memory-root-consistency.py
   ```

8. **Fast-forward the primary clone and rebuild.** The code half catches up to the data half you just moved.

   ```bash
   git -C ~/Antigravity/agentm pull
   bash ~/Antigravity/agentm/install.sh --daemon
   ```

   This rebuilds both `agentmd` and `agentmdream` from the post-rename source. The build uses `CGO_ENABLED=0` for pure Go. See [Memory daemon reference § Building it](Memory-Daemon#building-it). It reloads `agentmd`. Bring the daemon back. Confirm it runs.

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.daemon.plist
   launchctl kickstart -k gui/$(id -u)/com.agentm.daemon
   agentmd status
   /doctor --live
   ```

   The `agentmd status` command and the doctor's install rows check `agentmd`'s own freshness. They do not check `agentmdream`'s freshness. The dreaming binary ships in the same rebuild. Nothing checks it by name. Confirm it separately.

   ```bash
   strings ~/.local/bin/agentmdream | grep -c '"projects"'
   ```

   A zero count means the installed binary predates this rebuild.

9. **Rebuild the ledger.** Measure what the night owes again.

   ```bash
   agentmd ledger --rebuild
   agentmd ledger --forget <target>   # once per target in step 2's pending list, under its new path
   agentmd enrich -dry-run
   ```

   The deep, light, and unchanged counts must match step 2. Any other movement means something wrote outside this plan.

10. **Re-key the two live state files the migration ignores.** History files keep the spelling of the day they were written. Examples include `enrich-runs.jsonl`, `tier-audits.jsonl`, and a finished migration's recorded plan. Do not edit them. Two files hold live pointers instead of history. They need a hand edit to the new spelling:

    - `~/.local/state/agentm/enrich-refusals.jsonl` — edit the `rel` field on each line.
    - `graph-snapshot-cross-check-state.json` (in the same engine state directory) — edit the `attempted` list.

11. **Finish the migration.**

    ```bash
    python3 scripts/migrate/root_casing.py --finish --plan <PLAN>
    ```

    This checks every post-condition at once. It looks for five lowercase root spaces. It confirms no Title Case or temporary directory remains. It verifies the index counts, the three settings files, and the three config keys. It checks both `MEMORY_VAULT_PATH` exports. It ensures the Python stack resolves the vault and the memory root correctly without an environment override. It writes `agent/memory/.root-casing-complete` only when all post-conditions hold. It prints the exact `git add`/`git commit` for that marker. Run the commands exactly as printed. A failure lists what is still wrong and writes nothing. Fix the failure. Run `--finish` again against the same plan.

12. **Run the link check again, and compare it against step 2 entry by entry.**

    ```bash
    MEMORY_ROOT=<memory-root> python3 harness/skills/memory/scripts/vault_lint.py --format json > /tmp/lint-after.json
    ```

    Compare `/tmp/lint-before.json` and `/tmp/lint-after.json` entry by entry and target by target. Two runs with the same count can still disagree on which link is unresolved. Green means no new unresolved links exist.

13. **Reindex the embeddings.**

    ```bash
    agentmd embed
    ```

    Every renamed path drops out of the dense arm until you run this.

14. **Bring the runner back.**

    ```bash
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.runner.plist
    launchctl kickstart -k gui/$(id -u)/com.agentm.runner
    ```

15. **Run the full gate battery.** Do this now. Do it again after any later crickets plugin update.

    ```bash
    bash scripts/check-all.sh
    ```

    Read what the retrieval gate expects before you call a flipped question drift. A question that named a root by its old path moved because of this migration.

## Verify

- Run `ls <vault>`. It lists five lowercase root spaces (`agent`, `calendar`, `personal`, `projects`, `standards`) and the two root notes.
- Run `git -C <vault> ls-files | grep -c '^Agent/'`. It reads `0`. The same command for `Calendar/`, `Personal/`, and `Projects/` reads `0`.
- Run `python3 scripts/check-no-title-case-roots.py`. It reads `clean`.
- Check `agent/memory/.root-casing-complete`. It exists at the memory root and is committed.
- Check the owed counts in `agentmd enrich -dry-run`. They match what step 2 recorded.
- Run `python3 scripts/check-memory-root-consistency.py`. It reads clean.
- Check the retrieval gate. It reads green.

## Troubleshooting

- **The dry run refuses.** A temporary name (`.<root>-tmp`) already exists. An earlier two-step command did not finish. Move it to its final name by hand first. Both a Title Case and a lowercase directory might exist for the same root. A Title Case directory came back beside the lowercase one. See the step below.
- **`--apply` refuses with "the vault root is not what the dry run read."** Something changed the root listing between the dry run and the apply. Re-run the dry run from step 3. Apply the fresh plan.
- **`--apply` refuses on the confirm count.** You typed the wrong `--confirm-count`. The vault moved. Copy the printed `--apply` command verbatim instead of retyping it.
- **`--apply` refuses on staged changes or a live writer.** Step 6's hand commit must stay restricted to the renames. Any other staged change blocks it. Commit or unstage the extra changes first. A live writer means step 1 did not fully quiesce. Check `pgrep -f memory-reflect` again.
- **A two-step command didn't land.** Read `<engine state dir>/root-casing/journal-<run_id>.json`. The `renames` list names the root and its `stderr` output. Move the stuck temporary name to its final name by hand. Run `--finish` again. The `--finish` command checks the vault as it stands instead of the journal's claim.
- **A Title Case directory came back after `--finish`.** Something wrote to the old path again. Quiesce the system completely. Remove the empty Title Case directory. Fold its files into the lowercase root if it contains data. Run `--finish` again.
- **Drive shows a duplicate folder.** You might see `agent (1)`. This is the design's own re-audit trigger for the case-only-rename mitigation. Check Drive's web view before changing anything else.
- **You need to undo an applied run.** Run `python3 scripts/migrate/root_casing.py --revert <RUN_ID>`. It moves every root the run renamed back. It moves the newest first. It removes the completion marker. It refuses and restores nothing if a renamed root is already gone. It refuses if a Title Case name is back beside its lowercase twin. It refuses if a temporary name still exists. It refuses if a rewritten settings file no longer holds what the run wrote. The config keys and the `MEMORY_VAULT_PATH` exports are not reverted. Put them back by hand. Set them back to what they were before step 7.

## Related

- [scripts/migrate/root_casing.py](https://github.com/alexherrero/agentm/blob/main/scripts/migrate/root_casing.py) — the migration this page runs.
- [CI gates reference § check-no-title-case-roots](CI-Gates) — the gate the code half satisfies. It stays enforcing once this data run finishes.
- [Run the maps and root notes migration](Run-The-Maps-And-Root-Notes-Migration) — the sibling migration whose shape this page follows.
- [Run the card backfill](Run-The-Card-Backfill#steps) — steps 5 and 8. Step 9 above reuses this ledger-rebuild-and-reforget shape.
- [AgentM Vault design](../designs/agentm-vault) — the governing design. The root casing is step 7 of its structural moves.
