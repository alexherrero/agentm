# How to back the vault with Google Drive

> [!NOTE]
> **Part of** the [Vault Storage & Presentation Design](agentm-vault-storage-presentation) — Drive is the design's **simple mode**.
> **Goal:** Back up the Obsidian vault and sync it across your devices with Google Drive, with sync running in the background.
> **Prereqs:** Google Drive for Desktop installed and signed in; Obsidian on each device.

> [!NOTE]
> **Drive is a file mirror, not the vault's history.** The memory daemon keeps the vault in a local git repository and commits as notes change, so version history is always on. Drive adds sync to your other devices; an off-device backup of the history is a scheduled `git push` to a private remote you choose. What you trade with Drive is in [Drive's trade-offs](#drives-trade-offs-and-keeping-git-alongside) below.

By default a vault lives only on the machine that created it — it isn't backed up, and it isn't synced anywhere. To back it up and reach it from your other devices, you enable one transport: **Google Drive or git.** This page is the Google Drive route.

Drive's appeal is that sync needs no setup: drop the vault in your Drive folder and every signed-in device sees the changes, with nothing to run and normal mobile access through the Drive app. The cost is that Drive keeps no real history, so the vault's history comes from a git repository beside it, which the memory daemon commits to; how to set that up is in [Drive's trade-offs](#drives-trade-offs-and-keeping-git-alongside) below.

Obsidian itself is a separate, optional add-on that sits on top of *either* a Drive- or git-backed vault — see [Set up Obsidian on the vault](Use-Obsidian-With-The-Vault). This page only sets up the Drive transport.

## Prerequisites

- Google Drive for Desktop, installed and signed in, with a synced *My Drive* on each computer.
- Your vault folder (the directory holding the markdown).
- *Optional:* Obsidian on each device, if you want the Obsidian view ([Set up Obsidian on the vault](Use-Obsidian-With-The-Vault)) — the vault syncs with or without it.

## Steps

1. **Find your synced Drive folder.** On macOS, Drive for Desktop mounts it at:

   ```
   ~/Library/CloudStorage/GoogleDrive-<your-account>/My Drive
   ```

   Confirm it exists and shows as synced in the Drive menu-bar app.

2. **Move the vault into Drive.** Move (don't copy) the vault folder somewhere inside *My Drive*:

   ```sh
   mv ~/path/to/Vault "$HOME/Library/CloudStorage/GoogleDrive-<your-account>/My Drive/Obsidian/"
   ```

   Wait for the Drive app to report the folder fully uploaded before continuing.

3. **Point agentm at the moved vault** so the engine resolves the new location:

   ```sh
   python3 scripts/agentm_config.py --vault-path "$HOME/Library/CloudStorage/GoogleDrive-<your-account>/My Drive/Obsidian/Vault"
   ```

4. **Confirm the derived stores stay out of Drive.** agentm keeps its derived SQLite stores device-local at `~/.agentm/memory/_meta/` — outside the vault, so Drive never syncs them and there is nothing to exclude. SQLite on cloud sync is a known corruption pattern, which is why the split exists. Verify they're there, not under *My Drive*:

   ```sh
   ls ~/.agentm/memory/_meta/    # derived stores live here, on this machine only
   ```

5. **(Optional) Open the vault in Obsidian** on this computer: *Open folder as vault* → select the folder you moved into *My Drive*. Do this only if you use Obsidian ([Set up Obsidian on the vault](Use-Obsidian-With-The-Vault)); the vault syncs regardless.

6. **Add your other devices.** On each other computer: install Drive for Desktop, sign in, and let *My Drive* finish syncing — that propagates the vault. On phones: install the Google Drive app to sync the folder. To read the vault on a device, open the synced folder in Obsidian (or any editor) — optional, per step 5.

7. **Verify.** Edit a note on one device; after the Drive app reports the change synced, confirm it appears on another device.

## Drive's trade-offs (and keeping git alongside)

**What git adds, and why it sits beside Drive.** Drive keeps no history you can roll back past its trash window. git does: per-commit history, rollback, and an off-device backup through a push. The memory daemon commits every change it sees to the vault's git repository, so once the repository exists, history is always on. The daemon never creates one: a vault that isn't a repository shows as a missing capability on every status surface.

**Make the repository with its object database outside Drive.** Drive can't exclude a subdirectory, so keep `.git`'s contents out of the synced folder and leave only the pointer file in the tree:

```sh
git init --separate-git-dir ~/.agentm/vault-git   # working tree stays in Drive; .git stays local
```

Drive then mirrors the notes, and git keeps their history. For an off-device copy of that history, add a private remote and push to it on a schedule; `agentmd status` reports the last push. Edit through one device at a time: Drive and git don't know about each other, so the same note edited on two machines before Drive settles is a conflict git can't see.

## Troubleshooting

Most Drive issues are a file that got synced and shouldn't have been. A cleanup helper *(planned — not built yet)* will handle them in one pass — run it yourself, or ask the agent to:

```sh
python3 scripts/vault_drive_cleanup.py --vault "<your vault path>"
```

It (a) lists each Drive `(conflicted copy)` sibling and removes it once you've merged, (b) removes any `*.db` / `*.sqlite*` index file that ended up inside the vault (the index belongs at `~/.agentm/memory/_meta/`), and (c) confirms the vault is under *My Drive* and the index is not. Specific cases:

- **`(conflicted copy)` files appear**: a note was edited on two devices between syncs. Merge the two by hand, then let the helper delete the copy.
- **Search or recall breaks after moving the vault**: the vault path changed — re-run step 3 so agentm points at the Drive location.
- **A device shows stale notes**: Drive hasn't finished syncing — check the Drive app's status before assuming a problem.

## Related

- [Vault Storage & Presentation Design](agentm-vault-storage-presentation) — where Drive sits as the design's simple mode, and the full reasoning for one transport per folder.
- [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) — the git-backed option that was planned and closed on 2026-11-03; the daemon's own commits replace it.
- [Memory↔Storage Seam](memory-storage-seam) — why the search index is device-local (`~/.agentm/memory/_meta/`), so Drive never touches it.
- [Installer CLI reference](Installer-CLI) — the `agentm_config.py` config CLI used in step 3 (the `--vault-path` setter).
