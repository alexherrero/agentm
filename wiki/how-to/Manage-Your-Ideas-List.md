# How to manage your ideas list

> [!NOTE]
> **Status: implemented** — shipped by agentm-vault plan 13, the ideas surface.
> **Goal:** Adopt the generated `Ideas.md` once, then add, regroup, and retire ideas by editing their cards — never the file itself.
> **Prereqs:** `MEMORY_ROOT` set. `agentmdream` built and reachable (`~/.local/bin/agentmdream`, or built from `daemon/` — see [Memory daemon reference § the dreaming binary](Memory-Daemon#the-dreaming-binary-agentmdream)). At least one idea filed, or ready to file one — see [Drop a card in the inbox](Drop-A-Card-In-The-Inbox).

`Ideas.md`, at the vault root, is a list the dreaming binary rebuilds every night over the idea cards in `personal/ideas/`. Everything from the top of the file through your own end marker is yours, copied into every rebuild byte for byte; below it, the list is entirely the night's — one heading per group your cards carry, one line per idea, and the dismissed ones set apart. You never edit the list directly. You edit the cards, and the list follows.

## Steps

1. **Adopt `Ideas.md` once.** Skip this step if it already carries a `<!-- ideas:intro:start -->` marker — check with `agentmdream ideas`, below. Otherwise, write the introduction you want to keep — just the preamble text, not a title (`# Ideas` is added for you) and not the list — to a file, then preview before committing to it:

   ```bash
   agentmdream ideas -intro my-ideas-intro.md
   ```

   This prints the whole rendering — your introduction between the markers, the current list below it — and writes nothing. Read it. When it looks right, make the write:

   ```bash
   agentmdream ideas -intro my-ideas-intro.md -write
   ```

   This is the one deliberate replacement of a hand-kept `Ideas.md`. From here, the markers are your standing permission for the nightly rebuild, and deleting either one withdraws it — the file goes back to untouched until you adopt it again.

2. **Add an idea.** Either path lands it the same way:

   - From a chat surface's inbox card: `python3 harness/skills/memory/scripts/inbox_review.py --file <name> --type idea --area <group>` (see [Drop a card in the inbox](Drop-A-Card-In-The-Inbox)) — `--type` is optional when the card already carries `type: idea`.
   - Directly: create `personal/ideas/<slug>.md` yourself, with at least `title`, `type: idea`, and `area: <group>` in its frontmatter.

   `<group>` is one lower-case word, hyphens allowed — it becomes the card's `area:` and the heading it lands under.

3. **Regroup an idea.** Edit `area:` on its card by hand. There's no separate registry of groups to update — the heading in `Ideas.md` is generated from whatever values the cards carry, so renaming or merging a group is this one edit, repeated per card it applies to.

4. **Retire an idea.** Add `dismissed: YYYY-MM-DD` to its card. Nothing deletes it — the card stays on disk in `personal/ideas/`, and reappears on the live list the moment you remove the date.

5. **See the change land.** The nightly `agentmdream run -apply` rebuilds `Ideas.md` as one of its jobs, right alongside the maps. To see it sooner:

   ```bash
   agentmdream ideas -write
   ```

   A rebuild over a folder that hasn't changed since the last one writes nothing — running it again is harmless.

## What the night does to an idea card

- **It enriches it.** An idea card joins the same nightly pass as any other card, in its own turn, and may add a summary, tags, related links, `importance_proposed`, and — on a deep pass — its own dated section under `## Added by dreaming`.
- **It never re-grades it.** `status`, `filing_confidence`, `title` and `type` stay exactly what the card carries. The card never sinks to `dormant`, whatever the pass thinks of it.
- **It never gives the card a lifecycle.** `personal/` has no aging axis, and an idea card is the one kind of card that gets no default `lifecycle` written onto it either.
- **It never renames the card.** The filename you gave it is the one `Ideas.md` links by.

Full mechanism: [Memory daemon reference § Idea cards](Memory-Daemon#idea-cards).

## Verify

- `Ideas.md` opens with your introduction, between the two markers, byte for byte what you gave it.
- Below the end marker, one `## <area>` heading per group your cards actually carry — alphabetical, with cards carrying no `area:` last, under "no group yet" — each idea a line like `- [[a-thought-from-the-couch|A thought from the couch]] — <summary>`.
- A dismissed idea sits inside one collapsed `> [!note]- Dismissed (N)` callout at the end, not under its old heading.
- `agentmdream ideas` (no flags) prints the rendering it would write next, and, on stderr, a line naming the card and group counts and whether a rebuild would change anything.

## Troubleshooting

- **`Ideas.md` never changes, however many ideas I file.** It probably isn't adopted yet. `agentmdream ideas` (no flags) says `nothing to render — Ideas.md carries no markers, so it has not been adopted and is not written` when that's why — go back to step 1.
- **An idea I filed isn't on the list.** Confirm the card sits directly inside `personal/ideas/` — a subfolder is never read — and check its `area:`. A card with no `area:` still appears, under "no group yet", never silently dropped; if it's missing entirely, confirm the card's filename ends in `.md` and doesn't start with a dot.
- **I edited `Ideas.md` by hand below the end marker, and it came back.** That's the rebuild working as designed, not a bug — everything below the marker is regenerated from the cards whenever the folder changes. Make the edit on the card instead: retitle, regroup, or dismiss it there.
- **A dismissed idea is back on the live list.** Its card lost its `dismissed:` field. Enrichment always carries the field forward; a hand edit that rewrote the card's frontmatter from scratch is the likelier cause. Re-add the date.

## Related

- [Drop a card in the inbox](Drop-A-Card-In-The-Inbox) — filing an idea from a chat surface.
- [Memory daemon reference](Memory-Daemon#idea-cards) — the generator's mechanics and the stamp posture that keeps the night from re-grading a card.
- [Find your way around the vault](Find-Your-Way-Around-The-Vault) — where `personal/ideas/` sits in the layout.
- [AgentM Vault design](../designs/agentm-vault) — the ruling this page follows.
