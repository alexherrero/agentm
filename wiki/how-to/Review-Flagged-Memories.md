# How to review flagged memories

> [!NOTE]
> **Goal:** Work through flagged notes — a probable duplicate, a same-key contradiction, an unfiled capture, or anything filed at low confidence — using the generated needs-review page instead of hunting through the vault by hand.
> **Prereqs:** a vault with a `memory/` tree (any populated class works) and `python3` on `PATH`. Regenerating the page needs no running daemon.

## Steps

1. **Open the needs-review page, or regenerate it first.** It lives at `<memory-root>/memory/mocs/needs-review.md`. The weekly dream cycle rebuilds it; you can also rebuild it on demand:

   ```bash
   python3 harness/skills/memory/scripts/needs_review.py --vault <memory-root> --write
   ```

   Drop `--write` to print the count without touching the file, or add `--json` for the machine-readable form. The page groups every flagged note into up to four sections, in the order a reviewer should meet them: **Probable duplicates**, **Same key, different body**, **Unfiled captures**, **Filed at low confidence**. Each line names the note and explains its presence — a duplicate names its twin, an unfiled capture names its wait time.

2. **Work the duplicates and contradictions first.** You find a probable duplicate filed beside its twin. Open both notes and choose one action:

   - Merge them by hand.
   - Leave the newer one to age out on its own.

   A same-key entry means two notes assert different values for the same thing. Decide which one is current, then supersede the other:

   - Set the note's `lifecycle: superseded`.
   - Add the `supersedes:` pointer to the note it replaces.

   See [Memory daemon reference](Memory-Daemon) for both fields.

3. **Type the unfiled captures.** These arrived through the capture front door with nobody standing behind a type. Take these actions:

   - Read the note.
   - Give it a real `type:` if the contract's default guess is wrong.
   - Let the next enrichment pass (`agentmd enrich`) pick it up.

   An unfiled note is already indexed and searchable, carrying a rank penalty until enrichment clears it. Reviewing it sooner just gets it there sooner.

4. **Confirm or re-file whatever's left at low confidence.** These notes have a real type, but were filed below the pass's confidence floor. Take no action if the note reads right as filed — enrichment raises `filing_confidence` to `high` once it judges the note at or above its own floor. If the note is wrong, choose one action:

   - Edit the note directly.
   - Supersede it.

   You make no changes to the needs-review page itself.

5. **Re-run step 1 to confirm the count dropped.** An entry clears when you re-judge the note it points at:

   - Raise it to `active` at high confidence.
   - Hand-edit it.
   - Supersede it.

   The page is overwritten whole on every regeneration — there is nothing to clear on the page itself.

## Related

- [Read the nightly scorecards](Read-The-Nightly-Scorecards) — the scorecard's "needs review" line tracks this same count on a nightly schedule.
- [Memory daemon reference](Memory-Daemon) — the enrichment pass, and the `filing_confidence` / `lifecycle` fields these flags read.
- [Vault write protocol](Vault-Write-Protocol) — what a write stamps by default, and the baseline trust level for a plain capture.
