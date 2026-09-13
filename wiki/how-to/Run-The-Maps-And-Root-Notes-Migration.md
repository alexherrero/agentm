# How to run the maps and root notes migration

> [!NOTE]
> **Goal:** Move the vault onto the shape agentm-vault plan 07 leaves the code expecting: `Home.md` and `Filing.md` retired for the generated root map, numbered map pages gone because a type's page paginates in place, and a map for each calendar year.
> **Prereqs:**
>
> - The code has merged to `main` and deployed in one step: your clone fast-forwarded, which makes the Python scripts live, and `agentmd` and `agentmdream` rebuilt from the same commit, with the daemon restarted on the new binary.
> - You have shell access to the machine the vault and the daemon run on.
> - The configured vault path resolves to the vault you're migrating (or `MEMORY_ROOT` names its memory root for this shell).
> - You run this outside the nightly `02:00`–`06:00` window.
> - If `Filing.md` exists in this vault, a reviewed draft of `index.md` is ready that folds `Filing.md`'s write-authority table into it.

`scripts/migrate/maps_and_root_notes.py` moves the vault to match the code. The numbered map pages, `Home.md` and `Filing.md` go. A stray day note at the calendar root moves into its year with its body unchanged. Every link naming what went is repointed where it may be, and `index.md` takes the reviewed draft. The script runs as a dry run, then `--apply` against a confirmed count, then `--finish` once the maps have been regenerated. `--revert RUN_ID` undoes an applied run.

## Steps

1. **Quiesce the runner and the daemon.** Both write into the vault outside the nightly window, and a write between the dry run and the apply makes the apply refuse:

   ```bash
   launchctl bootout gui/$(id -u)/com.agentm.runner
   launchctl bootout gui/$(id -u)/com.agentm.daemon
   ```

2. **Record what the night owes before you change anything.** A class card whose link the migration repoints no longer matches its enrichment fingerprint, so the night owes it again. Step 11 compares against what you save here:

   ```bash
   agentmd enrich -dry-run
   agentmd ledger --pending --limit 0 --json > /tmp/pending-before.json
   ```

3. **Dry-run the migration.** Nothing on disk changes yet:

   ```bash
   python3 scripts/migrate/maps_and_root_notes.py --index-draft <path-to-reviewed-index-draft>
   ```

   Omit `--index-draft` only if `Filing.md` doesn't exist in this vault. A type's page under the page threshold is held and reported, not deleted; add `--also-delete TYPE` (repeatable) only when you want that page gone.

   The dry run prints a summary (files, held pages, repointed links, whether `index.md` has a draft) and then a `manifest:` section: one line per file that goes or moves, each naming why it goes and the `git checkout` command that restores it. It records the plan under the engine state directory and prints the exact `--apply` command to run next.

4. **Paste the printed manifest into the plan's progress file before anything goes.** It is the record of what is about to go and how to bring any one file back by hand, apart from `--revert`.

5. **Apply it**, using the exact command the dry run printed:

   ```bash
   python3 scripts/migrate/maps_and_root_notes.py --apply --plan <PLAN> --confirm-count <N>
   ```

   It refuses if the vault no longer matches the dry run, or if your count doesn't match the plan's, and writes nothing either way. On success it reports `journal matches the dry run: yes`. On `NO`, stop and read the journal (see Troubleshooting).

6. **Bring the daemon back.** The dreaming pass and the embeddings run against a live daemon, as they do every night. The runner stays stopped until step 13, because its hourly jobs write to cards:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.daemon.plist
   launchctl kickstart -k gui/$(id -u)/com.agentm.daemon
   ```

   Confirm it with `agentmd status`; `launchctl list` alone isn't proof (see [Memory daemon reference](Memory-Daemon)). Once it is running, the daemon commits the migration's changes to the vault.

7. **Regenerate the maps with one pass by hand, reading its report first.** The pass runs every dreaming job, not only the maps, so see what it would change before you let it:

   ```bash
   agentmdream run -force
   agentmdream run -force -apply
   ```

   `-force` skips the pass's gate so it runs now. The first command changes nothing and prints what each job would write. Expect the maps (`moc-root.md`, `moc-memory.md` and each type's page) and the calendar year maps. Anything else it would write, such as a collapsed copy or a date gloss, rewrites a card; note which cards, for step 11.

8. **Regenerate needs-review:**

   ```bash
   python3 harness/skills/memory/scripts/needs_review.py --vault <memory-root> --write
   ```

   The nightly Python cycle (`dream.py`) does this too; run it here rather than waiting for the night.

9. **Finish it:**

   ```bash
   python3 scripts/migrate/maps_and_root_notes.py --finish --plan <PLAN>
   ```

   This checks every post-condition the gates will enforce: the three named maps exist, `mocs/` and the calendar root hold their shape, each year's map lists exactly that year's facet notes, `moc-root.md` lists every area's map, and no note anywhere still names what went. It writes the marker only when all of them hold. A failure lists what's wrong and writes nothing; fix it and re-run `--finish` against the same plan.

10. **Commit the marker by hand.** It's a new dot-named file (`<memory-root>/memory/.maps-and-root-notes-complete`), which the daemon does not commit on its own. `--finish` prints the exact `git add` and `git commit` command; run it as printed.

11. **Re-measure what the night owes:**

    ```bash
    agentmd enrich -dry-run
    ```

    The deep count should match step 2. The light count should rise by one for each class card the plan's `links` list rewrote: a rewritten card is owed a light pass, and that is expected. Any other movement means a card was rewritten outside the plan, by step 7's pass or by another writer. Then follow [Run the card backfill](Run-The-Card-Backfill#steps) steps 5 and 8 against step 2's pending list, and also forget each card this run rewrote, so the night still owes it.

12. **Reindex the embeddings:**

    ```bash
    agentmd embed
    ```

    Every deleted or moved path drops out of the dense arm until this runs, and skipping it reads as a retrieval regression.

13. **Bring the runner back:**

    ```bash
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.runner.plist
    launchctl kickstart -k gui/$(id -u)/com.agentm.runner
    ```

14. **Run the full gate battery:**

    ```bash
    bash scripts/check-all.sh
    ```

    The battery runs `check-root-notes`, `check-calendar-root` and the retrieval gate (see [CI gates reference](CI-Gates)). The retrieval gate names six flipped question ids and says how many more. If it moves, read what each flipped question expects before calling it drift: a question that expects a map, or a note this run moved, moved because of the migration.

## Verify

- `python3 scripts/check-root-notes.py` and `python3 scripts/check-calendar-root.py` both report `clean` (see [CI gates reference](CI-Gates)).
- `memory/.maps-and-root-notes-complete` exists at the memory root, and is committed.
- `memory/mocs/moc-root.md` lists every area's map, and `memory/mocs/moc-memory.md` lists every memory type.
- The deep count in `agentmd enrich -dry-run` matches step 2, and the light count differs only by the cards this run rewrote.
- `bash scripts/check-all.sh` passes locally.

## Troubleshooting

- **The dry run plans nothing.** `Home.md`, `Filing.md` and every numbered map page are already gone. `--finish` still needs an applied journal before it writes the marker, so apply the empty plan with `--confirm-count 0`, then run steps 7 to 10.
- **`--apply` refuses with "the vault is not what the dry run read."** Something wrote to the vault between the dry run and the apply. Re-run the dry run (step 3) and apply the fresh plan.
- **`--apply` refuses on a file-count mismatch.** You typed the wrong `--confirm-count`, or the vault moved. Re-run the dry run and copy its printed command verbatim.
- **`--apply` reports `journal matches the dry run: NO`.** Part of the run didn't land as planned. Read `<engine state dir>/maps-and-root-notes/journal-<run_id>.json`: its `landed` object names which of the deletions, moves, links and index failed. Don't run `--finish` against this plan; `--revert` the run (below) and start again once you know why.
- **The dry run refuses the index draft.** A draft must carry the write-authority table exactly once and link no retired note. Fix the draft and re-run the dry run.
- **The dry run refuses a file with uncommitted changes.** A file the run would delete or move has changes the vault hasn't committed, so no commit would restore it as it is. Commit it in the vault, then re-run the dry run.
- **`--finish` reports post-conditions that fail.** Each line names what's still wrong: a missing map (steps 7 and 8 haven't run), a `mocs/` or calendar-root finding (see [CI gates reference](CI-Gates)), or a note that still names what went. `--finish` doesn't repoint links itself, so fix the note by hand. Re-run `--finish --plan <PLAN>` once the lines clear.
- **You need to undo an applied run.** Run `python3 scripts/migrate/maps_and_root_notes.py --revert <RUN_ID>`. It restores every file the run wrote and removes `memory/.maps-and-root-notes-complete` if it exists.

## Related

- [scripts/migrate/maps_and_root_notes.py](https://github.com/alexherrero/agentm/blob/main/scripts/migrate/maps_and_root_notes.py) — the migration this page runs.
- [CI gates reference § check-root-notes, check-calendar-root, check-class-directories, check-memory-root-shape](CI-Gates) — the gates this migration brings from report-only to enforcing.
- [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream) — the `mocs` and `calendar` jobs that write the maps `--finish` checks for.
- [Run the card backfill](Run-The-Card-Backfill) — the sibling migration whose shape this page follows, and whose ledger steps step 11 names.
- [AgentM Vault design](../designs/agentm-vault) — the governing design for the shape this migration brings the vault to.
