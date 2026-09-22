# How to capture from your phone

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-capture-phone-ingest-sweep.md` (FRIDAY ladder feature 4, capture part 3 of 3).
> **Goal:** Forward a link or a thought from the Claude app on your phone and have it become a fully-processed, recallable memory within roughly two sweep cycles — no manual step on the machine required.
> **Prereqs:** The **Capture** project set up in the Claude app with the Google Drive connector's Create-file permission approved ("Always allow" — see `wiki/designs/agentm-capture.md`'s Detailed Design § The ways in). `templates/jobs/capture-ingest-sweep.yaml` registered into `.harness/jobs/` on the machine that runs your vault (step 3 below) — until then, run the sweep by hand.

The phone path itself needs no new setup beyond the Capture project — that door already works (capture-front-door, FRIDAY ladder feature 2). This page covers what happens after you send something, and how to get the automated half running so you don't have to.

## Steps

1. **Send something from your phone.** Open the **Capture** project in the Claude app and send `capture this: <link>`, a bare thought, or `idea: <thought>`. You can add a trailing instruction like `tag:urgent` or `file-under:work` — these two are the only instructions this sweep executes automatically (see `dispatch_instruction()`, `harness/skills/memory/scripts/ingest_sweep.py:424-441`); anything else (e.g. "research this further, then file") is left for you to act on yourself, surfaced in the digest rather than executed.

2. **The candidate lands in your vault.** The Google Drive connector creates the file directly. Your machine can be asleep — the connector delivers the file when the machine wakes. Since agentm-vault plan 16 the phone's Capture project writes into `agent/inbox/`, the same drop folder every other chat surface uses ([Drop a card in the inbox](Drop-A-Card-In-The-Inbox)), and the sweep below **cannot reach that folder**: an inbox card waits for you at `/memory inbox`, not for an hourly job. The retired `memory/_inbox/` staging directory is no longer walked at all. What the sweep still owns is every capture filed at its class directory with `status: unfiled` — which is what the rest of this page describes.

3. **Register the sweep (one-time).**

   ```bash
   cp templates/jobs/capture-ingest-sweep.yaml .harness/jobs/capture-ingest-sweep.yaml
   ```

   `.harness/jobs/` is gitignored, so this is a local, per-machine step. The manifest ships `dry_run: true` — watch a real cycle before flipping it, matching every other new job template in this repo. Until you register it (or flip `dry_run`), run the sweep yourself: `python3 harness/skills/memory/scripts/ingest_sweep.py`.

4. **First cycle: fetch and stage.** Within an hour, the sweep fetches your link — or, for an Obsidian Web Clipper capture, skips the fetch, since it already has the full content. It patches your original candidate note in place, at its class directory. The patch sets `status: ingest_staged` and appends the fetched text under a `## Fetched content` heading. A staged candidate is excluded from recall by that status field — `recall.py` checks it, not the note's location (`stage_candidate()`, `ingest_sweep.py:270-347`).

5. **Second cycle: promotion.** A staged candidate that survives one full sweep cycle (≈1 hour) gets promoted on the next run. `ingest.ingest()` (unchanged from `/memory ingest`, see [Ingest an article](Ingest-An-Article)) writes the permanent, indexed entries to `memory/semantic/` — `kind: reference`, the class the filing contract routes it to. Your original candidate flips to `status: ingested` wherever it lives, and drops its `## Fetched content` section now that the real copy exists (`promote_candidate()`, `ingest_sweep.py:366-417`). From here it surfaces in ordinary recall like anything else.

6. **A resend isn't fetched or promoted twice.** The Drive connector can create files. It can't update or delete them, so an uncertain send sometimes lands twice. The sweep checks every candidate it can see — the flat notes under every class directory — for a matching `source_url` before fetching (`_find_duplicate_by_source_url()`, `ingest_sweep.py:247-267`). A resend is marked `status: ingest_duplicate` and pointed at the original: never re-fetched, never promoted separately.

## Why the delay

The first version of this design would have written the fetched content straight to permanent memory, same as an explicit `/memory ingest` call. A pre-merge review on the article-ingestion part (feature 3) found that bypasses this design's own staged/triage trust model for the one path that actually needed it: an automated fetch, with no human reviewing the specific link before it's fetched. The one-cycle staging window keeps the "processed within an hour" promise for the ordinary case, while giving at least one digest cycle where something wrong would be visible before it's trusted. An explicit, human-invoked `/memory ingest` call is unaffected by any of this — you named the source yourself, so it writes directly, same trust level as `memory_append`.

## Verify

- `StagingTests` and `PromotionTests` (`scripts/test_ingest_sweep.py`) prove the full lifecycle both ways: a staged candidate is confirmed recall-invisible (`recall._iter_entry_paths()` returns nothing for it), and the same candidate, after one cycle elapses, is confirmed recall-visible with no special-cased lookup.
- `test_same_cycle_resend_is_not_fetched_or_promoted_twice` proves the duplicate-resend case.
- `ActStepTests` proves the `tag:`/`file-under:` instructions execute, and that anything else — including adversarial strings crafted to look like a command — never executes, only surfaces.

## Troubleshooting

- **Nothing seems to happen.** Check the candidate's own `status` field:

  - `unfiled`, or the legacy `inbox` for anything the phone connector wrote — the sweep hasn't run yet, or the job isn't registered.
  - `ingest_staged` — the candidate is in its review window.
  - `ingested` — done, and it should be recallable.

  The candidate never moves house for any of this. Look for it at its class directory (`memory/semantic/` for a plain thought or a link), or in `_inbox/` for a phone capture.

- **A fetch failed.** The candidate stays at its current status — `unfiled` or `inbox` — with nothing recorded as lost. The sweep's digest surfaces the failure explicitly (`_render_digest()`, `ingest_sweep.py:627-668`). Fix the link, or drop the candidate by hand; the sweep retries it on the next cycle either way.
- **An idea capture (`idea: <thought>`) doesn't show up in `Ideas.md`.** It is never folded in automatically — since agentm-vault plan 16 it lands in `agent/inbox/` like any other phone capture (step 2, above) and waits for your review; since agentm-vault part 13 there is no automatic fold to opt into any more, and hand-editing `Ideas.md` below its markers doesn't stick either, since the file is rebuilt over `personal/ideas/` every night. File it explicitly, with its group: `python3 harness/skills/memory/scripts/inbox_review.py --file <name> --type idea --area <group>` (see [Drop a card in the inbox](Drop-A-Card-In-The-Inbox)). That lands the card in `personal/ideas/`; the next `agentmdream run` picks it up, or run `agentmdream ideas -write` yourself to rebuild `Ideas.md` sooner. See [Manage your ideas list](Manage-Your-Ideas-List) for the rest — including the one-time adoption `Ideas.md` needs before anything writes to it at all.

## See also

- [Ingest an article](Ingest-An-Article) — the explicit, human-invoked door this sweep's promotion step reuses.
- [Drop a card in the inbox](Drop-A-Card-In-The-Inbox) — filing a candidate once it lands, including an idea.
- [Manage your ideas list](Manage-Your-Ideas-List) — where a filed idea goes from here.
- [Memory MCP tools reference](Memory-MCP-Tools) — `memory_capture`'s field-level detail, the capture contract this sweep's candidates share.
- `wiki/designs/agentm-capture.md` — the full design, including the Trust Boundary section this page's "Why the delay" summarizes.
