# How to set up Obsidian on the vault

> [!NOTE]
> **Part of** the [Vault Storage & Presentation Design](agentm-vault-storage-presentation) — the optional presentation layer over either transport.
> **Goal:** Set up a comfortable Obsidian configuration over the vault — the right plugins, settings, and templates — so notes read and link well.
> **Prereqs:** Obsidian installed; the vault already backed by a transport ([vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) or [Back the vault with Google Drive](Back-The-Vault-With-Drive)).

The vault is a folder of markdown, so you can read and edit it with any text editor, the CLI, or a chat connector. Obsidian is an optional app on top of that folder: it adds a linked-notes view — graph, live backlinks, and Dataview dashboards — that makes a growing vault easier to navigate. The memory engine already writes the frontmatter and `[[wikilinks]]` Obsidian reads, so most of the value is there the moment you open the folder.

Obsidian sits on top of whichever transport you chose — git or Drive — and works the same either way: it reads the files, the transport moves them. It stays optional, so reach for it when the graph and dashboards help, and use a plain editor when that's enough (see [Running without Obsidian](#running-without-obsidian)).

## Prerequisites

- Obsidian installed (desktop; mobile optional).
- The vault folder, already (optionally) backed by [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) or [Back the vault with Google Drive](Back-The-Vault-With-Drive).

## Steps

1. **Open the vault in Obsidian.** Launch Obsidian → *Open folder as vault* → select the vault folder. On mobile, use the same *Open folder as vault* and pick the synced folder.

2. **Turn on community plugins.** Settings → *Community plugins* → *Turn on community plugins*. Then *Browse*, and install:

   - **Dataview** — read-only dashboards (counts by status, recent notes, orphans).
   - **Obsidian Git** — install this one only if your transport is git ([vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)); it pulls and commits from inside Obsidian.

   Enable each plugin after it installs.

3. **Configure Obsidian Git** (git transport only). Settings → *Obsidian Git*:

   - *Pull on startup* → on.
   - *Commit-and-sync interval* → e.g. 10 minutes.
   - *Pull updates on interval* → match the commit interval.

   This keeps the local clone current while you work, so your other devices and the autosync hook stay in step.

4. **Turn on the linking panes.** Settings → *Core plugins* → enable *Backlinks* and *Outgoing links*, then open them from the right sidebar — the vault is meant to be read as a linked graph.

5. **Install the templates pack** *(planned — the pack ships with this how-to once built)*. Copy the templates into a `Templates/` folder at the vault root, then Settings → *Core plugins* → *Templates* → set *Template folder location* to `Templates`. The pack's entry and map-of-content (MOC) templates match the agent's writing conventions, so your hand-written notes sit alongside agent-written ones.

6. **Check the vault's health** (recommended). Catch broken wikilinks, missing frontmatter, and orphaned notes as the vault grows:

   ```sh
   /doctor          # in a host that ships the doctor skill
   ```

   Fix what it flags, or ask the agent to.

## One vault inside another

Obsidian's vault and the memory engine's vault don't have to be the same folder. Obsidian treats whichever folder you opened as the vault — the one holding `.obsidian`. The engine's vault is the folder your storage config points at ([Choose a storage backend](Choose-A-Storage-Backend)); when the installer probes for one, it looks for the `_meta/repos.json` marker, not for `.obsidian`. So the engine's folder can sit as a subtree inside a bigger Obsidian vault, and the boundary stays strict.

Everything beside that subtree — personal notes, other projects — shows up in Obsidian's sidebar, search, and graph, but the engine ignores it: no reads, no writes, no indexing, no counts. Doctor checks, lint totals, and recall all scope to the engine's folder alone.

For a 1:1 view — a sidebar and graph that match what the engine sees — open the engine's folder as its own Obsidian vault (*Open folder as vault* → select it). The cost is a second `.obsidian` profile to configure (steps 2–5) and a vault switch to reach your other notes.

## Running without Obsidian

Obsidian is optional — the vault is plain markdown, so every workflow stands on its own without it:

- **A plain text editor** — open and edit the `.md` files directly.
- **The CLI or the agent** — capture and recall run through agentm directly.
- **A chat connector** — read the vault from Claude.ai or Gemini over the GitHub connector (git transport).

Obsidian adds graph view, live backlinks, and dashboards on top; pick it up when those earn their place.

## Troubleshooting

- **Broken wikilinks or orphaned notes pile up**: run `/doctor` to list them, then relink or remove.
- **Dataview dashboards show nothing**: the queried frontmatter fields are missing — recreate the note from a templates-pack template (step 5), which supplies them.
- **Obsidian Git stalls on mobile**: mobile is git's weak spot — use Working Copy + Obsidian on iOS instead (see [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)).

## Related

- [Vault Storage & Presentation Design](agentm-vault-storage-presentation) — where Obsidian fits as the optional presentation layer over either transport.
- [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) · [Back the vault with Google Drive](Back-The-Vault-With-Drive) — the transports Obsidian reads on top of.
- [Memory↔Storage Seam](memory-storage-seam) — the storage backend Obsidian reads through.
- [Vault lint checks](Vault-Lint-Checks) — what `/doctor` inspects on the vault (broken wikilinks, frontmatter, orphans).
