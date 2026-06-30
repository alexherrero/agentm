# How to back the vault with Google Drive

> [!NOTE]
> **Part of** the [Vault Storage & Presentation Design](agentm-vault-storage-presentation) — Drive is the design's **simple mode**.
> **Goal:** Back up the Obsidian vault and sync it across your devices with Google Drive, with sync running in the background.
> **Prereqs:** Google Drive for Desktop installed and signed in; Obsidian on each device.

> [!NOTE]
> **Google Drive is the recommended way to sync the vault right now.** It's the simplest setup and works today. A git-backed option ([vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)) is **forthcoming** — when it ships it adds version history, off-device backup, and a safe chat-write path; until then, Drive is the supported default. What you trade with Drive is in [Drive's trade-offs](#drives-trade-offs-and-keeping-git-alongside) below.

By default a vault lives only on the machine that created it — it isn't backed up, and it isn't synced anywhere. To back it up and reach it from your other devices, you enable one transport: **Google Drive or git.** This page is the Google Drive route.

Drive's appeal is effortless sync: drop the vault in your Drive folder and every signed-in device sees the changes, with nothing to run and smooth mobile access. The cost is that Drive keeps no real history; if that matters, a git-backed vault is the alternative (manual today, turnkey once [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) ships) — a short summary of what you trade is in [Drive's trade-offs](#drives-trade-offs-and-keeping-git-alongside) below.

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

4. **Confirm the search index stays out of Drive.** agentm keeps the vector index device-local at `~/.agentm/memory/_meta/` (filename `vec-index.db`) — outside the vault, so Drive never syncs it and there is nothing to exclude. Verify it's there, not under *My Drive*:

   ```sh
   ls ~/.agentm/memory/_meta/    # the index lives here, on this machine only
   ```

5. **(Optional) Open the vault in Obsidian** on this computer: *Open folder as vault* → select the folder you moved into *My Drive*. Do this only if you use Obsidian ([Set up Obsidian on the vault](Use-Obsidian-With-The-Vault)); the vault syncs regardless.

6. **Add your other devices.** On each other computer: install Drive for Desktop, sign in, and let *My Drive* finish syncing — that propagates the vault. On phones: install the Google Drive app to sync the folder. To read the vault on a device, open the synced folder in Obsidian (or any editor) — optional, per step 5.

7. **Verify.** Edit a note on one device; after the Drive app reports the change synced, confirm it appears on another device.

## Drive's trade-offs (and keeping git alongside)

**What a git-backed vault would add.** git gives per-commit history and rollback, an off-device backup that doesn't depend on Drive's trash window, and a safe chat-write path (propose-via-PR). Drive offers none of those: recovery is bounded by Drive's trash retention, concurrent edits land as `(conflicted copy)` files instead of merges, and chat connectors get read-only access. If any of those matter, you can wire git manually today, and [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) will make it turnkey once it ships.

**Keeping git alongside Drive — discouraged, but supported.** The clean setup is one transport per folder. You *can* keep a git repo on the same folder, but Drive and git are two sync engines that don't know about each other, so a file edited through both between syncs produces a Drive `(conflicted copy)` *and* git conflict markers at once. If you keep both anyway, the one rule is to hold `.git` outside the synced tree so Drive never replicates it:

```sh
git init --separate-git-dir ~/.agentm/vault-git   # working tree stays in Drive; .git stays local
```

The simpler path is to pick one — stay on Drive (above), or move to git ([vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)).

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
- [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) — the forthcoming git-backed option: history, off-device backup, and a chat propose-via-PR path.
- [Memory↔Storage Seam](memory-storage-seam) — why the search index is device-local (`~/.agentm/memory/_meta/`), so Drive never touches it.
- [Installer CLI reference](Installer-CLI) — the `agentm_config.py` config CLI used in step 3 (the `--vault-path` setter).
