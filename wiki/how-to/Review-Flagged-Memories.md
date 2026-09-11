# How to review flagged memories

> [!NOTE]
> **Goal:** Work through flagged notes — a probable duplicate, a same-key contradiction, an unfiled capture, or anything filed at low confidence — and the dream cycle's possible twins, shared keys and proposed facets, using the generated needs-review page instead of hunting through the vault by hand.
> **Prereqs:** a vault with a `memory/` tree (any populated class works) and `python3` on `PATH`. Regenerating the page needs no running daemon.

## Steps

1. **Open the needs-review page, or regenerate it first.** It lives at `<memory-root>/memory/mocs/needs-review.md`. The nightly dream cycle rebuilds it; you can also rebuild it on demand:

   ```bash
   python3 harness/skills/memory/scripts/needs_review.py --vault <memory-root> --write
   ```

   Drop `--write` to print the count without touching the file, or add `--json` for the machine-readable form. The page groups every flagged note into up to four sections, in the order a reviewer should meet them: **Probable duplicates**, **Same key, different body**, **Unfiled captures**, **Filed at low confidence**. Each line names the note and explains its presence — a duplicate names its twin, an unfiled capture names its wait time.

   Below the notes' own sections, a line reads "The sections below come from the dream cycle of `<date>`. Nothing acts on them but you." Up to three more sections follow: **Possible twins**, **Shared keys, different bodies**, and **Proposed facets**. The printed count adds them after `from dreaming:`.

2. **Work the duplicates and contradictions first.** You find a probable duplicate filed beside its twin. Open both notes and choose one action:

   - Merge them by hand.
   - Leave the newer one to age out on its own.

   A same-key entry means two notes assert different values for the same thing. Decide which one is current, then supersede the outdated one:

   - Set its `lifecycle: superseded`.
   - Add its `superseded_by:`, naming the note that replaces it.

   The current note needs no change — its `supersedes:` back-link is optional. See [Memory daemon reference](Memory-Daemon) for both fields.

3. **Type the unfiled captures.** These arrived through the capture front door with nobody standing behind a type. Take these actions:

   - Read the note.
   - Give it a real `type:` if the contract's default guess is wrong.
   - Let the next enrichment pass (`agentmd enrich`) pick it up.

   The line says which of two waits it is. `awaiting the batch` means the nightly enrichment batch has not judged the note yet. `judged below the floor on <date>` means the batch read it and scored it under its floor. The next move is yours.

   An unfiled note is already indexed and searchable, carrying a rank penalty until enrichment clears it. Reviewing it sooner just gets it there sooner.

4. **Confirm or re-file whatever's left at low confidence.** These notes have a real type, but were filed below the pass's confidence floor. Take no action if the note reads right as filed — enrichment raises `filing_confidence` to `high` once it judges the note at or above its own floor. If the note is wrong, choose one action:

   - Edit the note directly.
   - Supersede it.

   You make no changes to the needs-review page itself. Leaving one alone is not neutral forever: a note already judged below the floor once, then judged below it again after its body, the enrichment prompt, or the filing contract changes, sinks to `lifecycle: dormant` on that second pass. A pinned note and the two rule types, `preference` and `convention`, are exempt and stay listed here instead of sinking. See [Memory daemon reference § Enrichment](Memory-Daemon#enrichment).

5. **Work the dream cycle's sections.** The nightly `dream.py` run found these pairs and labels. It merges, supersedes and registers nothing itself:

   - **Possible twins** are two notes whose bodies are at least 92% alike. Merge them by hand, or supersede the one that should go with `lifecycle: superseded` and `superseded_by:`, as in step 2.
   - **Shared keys, different bodies** are notes that carry the same `slug:` and say different things. Decide which one is current and supersede the other.
   - **Proposed facets** are diary labels that recurred on three or more days in the last thirty. If one is a facet, register it under `facets:` in `standards/storage-rules.md`. If it isn't, leave it.

6. **Re-run step 1 to confirm the count dropped.** A note entry clears when you re-judge the note it points at:

   - Raise it to `active` at high confidence.
   - Hand-edit it.
   - Supersede it.

   The page is overwritten whole on every regeneration — there is nothing to clear on the page itself. The dream sections are the exception to an on-demand rebuild. They come from the last cycle's findings, so they clear on the next cycle that no longer finds the pair or the label. To see that sooner, run the cycle yourself:

   ```bash
   python3 harness/skills/memory/scripts/dream.py --vault-path <memory-root>
   ```

## Related

- [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards) — the morning note's *What needs you* shows counts and the first five items of several of these lists every morning, and the scorecard's "needs review" line tracks the same count.
- [Memory daemon reference](Memory-Daemon) — the enrichment pass, and the `filing_confidence` / `lifecycle` fields these flags read.
- [Memory daemon reference § the Python cycle](Memory-Daemon#the-python-cycle-beside-it) — what the nightly `dream.py` run checks and where it leaves its findings.
- [Vault write protocol](Vault-Write-Protocol) — what a write stamps by default, and the baseline trust level for a plain capture.
