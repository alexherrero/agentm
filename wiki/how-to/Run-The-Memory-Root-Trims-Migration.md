# How to run the memory-root trims migration

> [!NOTE]
> **Goal:** Move an existing vault's memory root to the shape agentm-vault plan 05 left the code expecting — the always-load tier at `standards/`, feature state under `Projects/agentm/`, and the engine's own files (sidecars, the repo registry, the dream exhaust) out of the vault entirely.
> **Prereqs:** Plan 05 has merged to `main`. You have shell access to the machine the vault and the daemon run on. `MEMORY_ROOT` (or the configured vault path) resolves to the vault you're migrating.

Code that ships with plan 05 already resolves these files through `vault_layout.py`: the new home first, the retired location as the fallback. That's what makes the migration safe to run whenever you get to it — nothing breaks before you run it, and nothing recreates a retired location afterward. `scripts/migrate/memory_root_trims.py` moves your vault's actual files to where the code already expects them. It's dry-run by default, and idempotent — a re-run after a partial apply finishes the remainder and touches nothing already done.

## Steps

1. **Quiesce the runner and the daemon.** Both write into the vault, and a write mid-move breaks the script:

   ```bash
   launchctl bootout gui/$(id -u)/com.agentm.runner
   launchctl bootout gui/$(id -u)/com.agentm.daemon
   ```

2. **Let the vault settle clean.** If your transport is vault-git, let the autosync hook finish its current commit and confirm nothing is pending (`git -C <vault> status --porcelain` prints nothing). On a Drive-backed vault, give sync a minute to catch up. The migration reads the vault as it finds it — a mid-sync vault can look like files are already gone.

3. **Dry-run the migration.** Nothing on disk changes yet:

   ```bash
   python3 scripts/migrate/memory_root_trims.py
   ```

   It reports four steps in order — the standards set, feature state to `Projects/agentm/`, engine files out, the twin and the empties — each line prefixed `would:` or `left:`. A `left:` line names something the script won't touch on its own: a name collision, or content it can't safely fold. Read those before you apply.

4. **Apply it.** Only `--apply` performs the moves:

   ```bash
   python3 scripts/migrate/memory_root_trims.py --apply
   ```

   Resolve any `left:` items by hand, then re-run the same command — the script moves only what it's sure is safe, and a second pass picks up the rest.

5. **Fast-forward your clone** to the merged plan-05 code, if you applied the migration from a worktree or an unmerged branch:

   ```bash
   git -C ~/Antigravity/agentm pull
   ```

6. **Rebuild the binaries.** `agentmd` and `agentmdream` — built together by the installer — both need the post-trims source: `index.OpenWithSidecar` and `PlanLifecycle`'s `sidecarDir` parameter didn't exist before this plan.

   ```bash
   bash ~/Antigravity/agentm/install.sh --daemon
   ```

   This rebuilds both binaries, reloads `agentmd`, and verifies it answers `/health` before returning (see [Refresh an installed harness](Update-Installed-Harness) for what else a plain re-run touches).

7. **Bootstrap and kickstart the runner.** The installer manages `agentmd`'s launch agent for you. The runner's job you bootout'd in step 1 needs bootstrapping back into launchd yourself, then a kickstart so it doesn't wait for its next scheduled window:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentm.runner.plist
   launchctl kickstart -k gui/$(id -u)/com.agentm.runner
   ```

   Don't trust `launchctl list` alone for either job — a job that launchd shows as loaded can still be dead on a held port (see [Memory daemon reference](Memory-Daemon)). Confirm the daemon with `agentmd status`.

8. **Reindex the embeddings:**

   ```bash
   agentmd embed
   ```

   The dense arm scores by path-scoped chunks. A bulk move without this step reads as a retrieval regression rather than a migration, if you're comparing before/after numbers.

9. **Verify the shape:**

   ```bash
   python3 scripts/check-memory-root-shape.py
   ```

   `clean` means every retired location is gone and every new one is in place. Anything else is named item by item — a `mixed` result usually means step 3's `left:` items still need resolving by hand, or a writer still spelled a retired path.

10. **Run the doctor** for the wider picture — hook wiring, payload copies, the project-json-pointers check:

    ```bash
    /doctor --live
    ```

## Verify

- `check-memory-root-shape` (above) reports `clean`.
- `bash scripts/check-all.sh` passes locally — `check-memory-root-shape`, `check-memory-root-consistency`, and `check-registry-hygiene` all run in that battery.
- `agentmd status` reports the daemon healthy and reading the new sidecar location.

## Troubleshooting

- **`check-memory-root-shape` reports `mixed`.** Read each named finding. It's either a retired location the migration left because of a `left:` collision — resolve the collision, re-run `--apply` — or a writer that recreated a retired path: a binary or script that wasn't rebuilt from the post-trims source. Re-check step 6.
- **A promotion or a pin silently does nothing.** `save --always-load` and `heat-policy pin` write into the entry's own class now, not into a directory. Check the entry's frontmatter for `lifecycle: pinned` (what `save --always-load` stamps since the card backfill, agentm-vault plan 06 — `always_load` itself is a retired field and is never written) or `heat_pin: true` rather than looking for a file under a `_always-load/` folder that no longer exists. Neither one recreates the retired pen.
- **The registry looks empty after the move.** `scripts/repo_registry.py`'s registry now lives at `<engine state dir>/repos.json`. A script or shell alias still pointed at `<vault>/_meta/repos.json` is reading (or writing) the retired copy. `check-registry-hygiene` and `scripts/check-memory-root-shape.py` both catch this.

## Related

- [scripts/migrate/memory_root_trims.py](https://github.com/alexherrero/agentm/blob/main/scripts/migrate/memory_root_trims.py) — the migration script this page runs.
- [CI gates reference § check-memory-root-shape](CI-Gates) — the gate this page's step 9 runs by hand.
- [Memory daemon reference](Memory-Daemon) — installing and reloading `agentmd`, and why `launchctl list` alone isn't proof of health.
- [Refresh an installed harness](Update-Installed-Harness) — the general rebuild-and-reinstall recipe steps 6 and 7 specialize.
- [AgentM Vault design](../designs/agentm-vault) — the governing design for the layout this migration moves the vault onto.
