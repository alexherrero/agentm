# How to run the projects migration

> [!NOTE]
> **Goal:** Move every project that still carries a `_harness/` directory onto the task layout agentm-vault plan 10 leaves the code expecting: each plan becomes `tasks/NNN-<verb-slug>/` with a `plan.md`, a `progress.md` and a `tracker.md`, and the rest of the harness directory lands in the project skeleton the design names.
> **Prereqs:**
>
> - agentm-vault plan 10, task 7 (the resolver half) is merged to `main` and deployed on this machine. `resolve_active_plan` has to read the task layout before any project loses its `_harness/` — a plan written the day after this move must not land back in the directory this move removes.
> - You have shell access to the machine the vault and the daemon/runner run on.
> - You run every command from the deployed primary clone, not a worktree — a worktree's gitignored `.harness/project.json` can still point at a stale vault.
> - You run this outside the nightly `02:00`–`06:00` window.
> - Every open task (`active` or `parked`) already has a tracker carrying a real State and a Next, or you write one during the run (step 6) — `--apply` refuses otherwise.
> - Obsidian is quit — verify with `pgrep -x Obsidian`. Google Drive is running, so it sees each move as it happens.

The script `scripts/migrate/projects_layout.py` follows `scripts/migrate/root_casing.py`'s shape: a dry run that only reads, records a plan, and prints a manifest with the reverse of every move; an `--apply` that refuses unless the tree still matches the dry run, the count is confirmed, and every open task's tracker is ready; a `--finish` that checks every post-condition and writes the completion marker; and a digest-checked `--revert`. Where it differs is the table it applies: every file under a project's `_harness/` matches exactly one row — a plan becomes `tasks/NNN-<verb-slug>/plan.md`, a queued plan becomes a task at `queued`, an archived plan becomes a numbered task at `done` with `closed:` from its archive date, briefs and designs and research bundles join their task or the project's own folders, the machine files and ledgers go to `desk/`, and `_index.md` becomes `charter.md`. A file the table names no destination for is listed as unmapped, and `--apply` refuses while any remain.

## Steps

1. **Quiesce the runner, the daemon, and Obsidian.** All three can write into a project while its files are mid-move.

   ```bash
   launchctl bootout gui/$(id -u)/com.agentm.runner
   launchctl bootout gui/$(id -u)/com.agentm.daemon
   ```

   Quit Obsidian by hand; confirm with `pgrep -x Obsidian`. `--apply` checks all four writers itself (the runner and the daemon via `launchctl`, Obsidian and a live `memory-reflect` hook via `pgrep`) and refuses if one is still running — this step gets ahead of that refusal instead of relying on it.

2. **Record what the night owes.** Run the link check and the ledger snapshot before anything moves.

   ```bash
   agentmd enrich -dry-run
   agentmd ledger --pending --limit 0 --json > /tmp/pending-before.json
   MEMORY_ROOT=<memory-root> python3 harness/skills/memory/scripts/vault_lint.py --format json > /tmp/lint-before.json
   ```

   Step 10 compares the owed counts against this snapshot; step 12 compares the lint snapshot entry by entry.

3. **Dry-run the migration.**

   ```bash
   python3 scripts/migrate/projects_layout.py
   ```

   Reads only. For each project it prints the file and plan-unit counts, then a count per table row, the slugs it isn't sure read verb-first, the status spellings it found and what each maps onto, the open tasks that need a hand-written State and Next before the move, any unmapped files or destination collisions (either refuses `--apply`), and the manifest — one line per move with its command and its reverse. It records the plan under `<engine state dir>/projects-layout/plan-<run_id>.json`.

4. **Correct the slugs that need it, and dry-run again.** A task's directory name is verb-first, and the script marks a slug for your correction rather than guessing at one — a wrong guess is a name a directory keeps for good. Write the corrections as a JSON map of `{"<project>/<source-file>": "<verb-slug>"}` and pass it back in:

   ```bash
   cat > /tmp/slugs.json <<'EOF'
   {"foo-project/PLAN-old-name.md": "rename-the-thing"}
   EOF
   python3 scripts/migrate/projects_layout.py --slugs /tmp/slugs.json
   ```

   Repeat until the review list reads what you're willing to accept — the migration doesn't require every slug to read as a verb, only lists the ones it isn't sure about.

   **Once the slugs are final, land the retrieval gate's remap rows.** The gold set expects notes that live under a `_harness/`, and this move takes them. Eleven of those expectations become numbered tasks, so their new paths depend on the slugs you just corrected — which is why the rows are generated from the recorded plan rather than written by hand, and why this comes after the corrections and not before:

   ```bash
   python3 scripts/migrate/projects_layout.py --gold-remap <PLAN>
   ```

   Paste the printed `_PROJECTS_REMAPS` table into `scripts/health/eval_retrieval_shipped.py` beside the remaps the earlier moves left there, and commit it to the plan's PR **before** step 9 deploys it. Never edit the gold set itself: it is frozen evidence, and a question that moved is named with its cause at score time. A non-zero exit means the table cannot place an expectation — read the paths it prints on stderr before going further.

5. **Paste the printed manifest into the plan's progress file.** Do this before anything moves. Each line carries the exact command that puts that one file back without using `--revert`.

6. **Satisfy the open-tracker gate.** For every task the dry run lists as open (`active` or `parked`) with no tracker, or a tracker with no State or no Next, `--apply` refuses. Write the tracker by hand, beside the flat pair it describes — `tracker-<slug>.md` next to `PLAN-<slug>.md` (`tracker.md` beside a singleton `PLAN.md`) — using `scripts/tracker.py`:

   ```bash
   python3 scripts/tracker.py new --title "<title>" --project <project-slug> \
     --objective "<the plan's Goal>" --next "<first step>" \
     --out <vault>/projects/<project>/_harness/tracker-<slug>.md
   python3 scripts/tracker.py transition <vault>/projects/<project>/_harness/tracker-<slug>.md \
     --to active --state "<what's true right now>" --next "<the next steps, in order>"
   ```

   Don't try to guess the task's eventual number — neither command takes one. `--apply`'s own stamping step corrects the tracker's `task:` field to the numbered directory name once the file actually moves there.

7. **Apply the migration.** Use the exact command and count the dry run — or its last corrected re-run — printed.

   ```bash
   python3 scripts/migrate/projects_layout.py --apply --plan <PLAN> --confirm-count <N>
   ```

   It refuses, and writes nothing, if the projects tree no longer matches what the dry run read, if your count doesn't match the plan's, if any file is still unmapped or any destination still collides, if the vault's index holds staged changes, if a writer from step 1 is live, or if the open-tracker gate from step 6 isn't clear. On success it reports how many files moved, how many trackers it wrote, how many links it rewrote and in how many notes, and how many worktree pointers it repointed, and it prints `journal matches the plan: yes`. It also lists any basename two moved notes share — read Troubleshooting before deciding those links are wrong.

8. **Commit the move by hand.** The `git mv` calls already staged the renames; the trackers `--apply` wrote and the links it rewrote are not staged yet.

   ```bash
   git -C <vault> add -A
   git -C <vault> commit -m "the projects migration: _harness/ dissolves"
   ```

9. **Deploy.** Fast-forward the primary clone so every session reads the resolver that already understands the task layout — it shipped ahead of this data run for exactly this moment (agentm-vault plan 10, task 7).

   ```bash
   git -C ~/Antigravity/agentm pull
   ```

   **Repoint the board pointers in the same step.** Two files name `items_source`, and the ledger it points at has just moved into `desk/`: each repo's own `.harness/project.json` — agentm's and crickets'. Neither vault twin carries the key. Edit each to the new `desk/board-items.json` path, and check the spelling while you are there: crickets' still names the vault root Title Case, which resolves today only because the disk is case-insensitive. The doctor's `project-json-pointers` rows are what read these, so a missed one shows up at step 15 and not before.

   ```bash
   python3 scripts/machinery_doctor.py | grep project-json-pointers
   ```

10. **Reindex, rebuild the ledger, and only then bring the daemon back.** The index and the ledger are both keyed by vault-relative path, and every moved file just changed its key — reading the ledger before the reindex tells a false story, the same order the root casing migration establishes.

    ```bash
    agentmd reindex
    agentmd ledger --rebuild
    agentmd ledger --forget <target>   # once per target in step 2's pending list, under its new path
    agentmd enrich -dry-run
    ```

    Compare the deep, light and unchanged counts against step 2's. Only once they match, bring the daemon back:

    ```bash
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.daemon.plist
    launchctl kickstart -k gui/$(id -u)/com.agentm.daemon
    agentmd status
    ```

11. **Finish the migration.** Run this from the deployed clone, not a worktree — same reason as the prereqs above.

    ```bash
    python3 scripts/migrate/projects_layout.py --finish --plan <PLAN>
    ```

    This checks every post-condition at once: no project (other than the entries already under `projects/completed/`) still carries a `_harness/` directory or an `_index.md`; every project has a `charter.md`; every task directory carries a three-digit prefix, a `plan.md`, and a `tracker.md` whose status is one of the five and whose `task:` names the directory it sits in; and every move the journal claims landed, with nothing moved back. It writes `agent/memory/.projects-migration-complete` only when none of that fails, and prints the exact commit for the marker — it's a new dot-file, which the daemon does not commit on its own. A failure lists what's still wrong and writes nothing; fix it and run `--finish` again against the same plan.

12. **Run the link check again, and compare it against step 2 entry by entry.**

    ```bash
    MEMORY_ROOT=<memory-root> python3 harness/skills/memory/scripts/vault_lint.py --format json > /tmp/lint-after.json
    ```

    Compare `/tmp/lint-before.json` and `/tmp/lint-after.json` entry by entry and target by target. Two runs with the same count can still disagree about which link is unresolved.

    **The lint is not enough on its own.** It resolves a wikilink by basename and skips `tasks/`, so a link
    written as a path (`[[projects/crickets/_index]]`), a basename several moved files share (`_index`,
    `progress`) or a relative Markdown link inside a moved note can break while the comparison reads clean.
    Resolve the links yourself, the way Obsidian does — relative path, vault path, path suffix, basename,
    case-insensitive — twice: against the files at the commit before the move, from each note's pre-move
    path, and against the vault now. A link that resolved then and does not now is a casualty, and the run's
    journal says where its target went. On the first run of this migration that check found 184 broken links
    in 96 notes after the lint comparison had reported none.

    **Sweep the prose pointers too.** The table rewrites links, not code spans, so a `**Part file:**` line, a
    charter's "load `_harness/PLAN.md` first" and any other path written in backticks still name the old
    place. `grep -rn '_harness/' <vault>/projects` finds them; the ones that instruct a session are the
    urgent ones, because following one recreates the directory.

13. **Reindex the embeddings.**

    ```bash
    agentmd embed
    ```

    Every moved path drops out of the dense arm until you run this.

14. **Bring the runner back.**

    ```bash
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.runner.plist
    launchctl kickstart -k gui/$(id -u)/com.agentm.runner
    ```

15. **Run the full gate battery.**

    ```bash
    bash scripts/check-all.sh
    ```

## Verify

- Run `git -C <vault> ls-files | grep -c '/_harness/'`. It reads `0` — no project's `_harness/` is still tracked.
- Run `ls <vault>/projects/<a-migrated-project>`. It shows `charter.md` and `tasks/`, and no `_harness/` or `_index.md`. It shows no project-level `tracker.md`: the table moves files, and a project's tracker is new content that belongs to the project-documents plan.
- Check `agent/memory/.projects-migration-complete`. It exists at the vault root and is committed.
- Check the owed counts in `agentmd enrich -dry-run`. The deep count rises by the number of records the move made eligible — anything that is now a charter, or sits under `decisions/`, `designs/` or `research/` directly inside a project — and nothing else moves. The light and unchanged counts hold: `ledger --rebuild` recovers each card's row from the corpus as it stands, so it absorbs the link rewrite rather than owing a pass for it. Predicting an increase there is predicting something the next step erases.
- From a migrated project, with no `.harness/active-plan` marker set, `python3 scripts/harness_memory.py resolve-active-plan` (no `--plan`) exits `4` with nothing on stdout — the bare-call refusal agentm-vault plan 10's resolver half added, now with a project to exercise it against.
- Check the retrieval / docs gates in `scripts/check-all.sh`. They read green.

## Troubleshooting

- **The dry run lists slugs needing review.** Not a refusal — write a `--slugs` correction (step 4) for the ones you want renamed, or accept the derived name for the rest. `--apply` doesn't require the list to be empty.
- **`--apply` refuses with "the projects tree is not what the dry run read."** Something changed since the dry run. Re-run the dry run from step 3 and redo any slug corrections against the fresh plan.
- **`--apply` refuses on the confirm count.** Copy the printed `apply:` command verbatim instead of retyping `--confirm-count`.
- **`--apply` refuses with unmapped or colliding files.** At least one file's row names no destination, or two files want the same one. This is the table's own gap, not something to work around by hand — it needs a fix in the script before this run can proceed.
- **`--apply` refuses on the open-tracker gate.** Go back to step 6 for the task(s) it names.
- **`--apply` refuses on staged changes or a live writer.** Commit or unstage whatever else is staged; step 8's commit must stay restricted to the migration's own moves. A live writer means step 1 didn't fully quiesce — check `pgrep -f memory-reflect` again.
- **A basename two moved notes share.** Hundreds of plans all becoming `plan.md` means a basename link can no longer name one note — `--apply` rewrites a link by basename into a path link with its words kept, but when two moved notes shared a basename it leaves both untouched rather than guess which one a reader meant, and lists them in its output. Read the notes it names and repoint those links by hand if you want them fixed.
- **A worktree or the main clone is bound to a plan that got renamed.** `--apply` repoints every `worktree-for-<slug>` pointer and the `.harness/active-plan` marker it finds under the registered repos (or the two default clones when no registry is reachable). A clone outside both still names the old slug afterward — edit its `.harness/active-plan` to the new task directory name by hand.
- **You need to undo an applied run.** Run `python3 scripts/migrate/projects_layout.py --revert <RUN_ID>`. It puts every move back, newest first, and refuses and restores nothing if a destination is already gone, a source is already back, or a tracker, a rewritten link, or a stamped `task:` field no longer holds what the run wrote — something else changed it since.

## Related

- [scripts/migrate/projects_layout.py](https://github.com/alexherrero/agentm/blob/main/scripts/migrate/projects_layout.py) — the migration this page runs.
- [Run the root casing migration](Run-The-Root-Casing-Migration) — the sibling migration whose shape this page follows.
- [Named plans](Named-Plans) — the resolver this migration's data matches (agentm-vault plan 10, task 7): placement, lookup by either form, and the exit-4 contract.
- [Process seam](Process-Seam) — `state_path`'s own exit-4 case, the same bare-call refusal from the process side.
- [AgentM Vault design](../designs/agentm-vault) — the governing design. "Projects and tasks" is session 4's decided section this migration implements.
