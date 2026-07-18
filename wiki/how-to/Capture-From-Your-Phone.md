# How to capture from your phone

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-capture-phone-ingest-sweep.md` (FRIDAY ladder feature 4, capture part 3 of 3).
> **Goal:** Forward a link or a thought from the Claude app on your phone and have it become a fully-processed, recallable memory within roughly two sweep cycles — no manual step on the machine required.
> **Prereqs:** The **Capture** project set up in the Claude app with the Google Drive connector's Create-file permission approved ("Always allow" — see `wiki/designs/agentm-capture.md`'s Detailed Design § The ways in). `templates/jobs/capture-ingest-sweep.yaml` registered into `.harness/jobs/` on the machine that runs your vault (step 3 below) — until then, run the sweep by hand.

The phone path itself needs no new setup beyond the Capture project — that door already works (capture-front-door, FRIDAY ladder feature 2). This page covers what happens after you send something, and how to get the automated half running so you don't have to.

## Steps

1. **Send something from your phone.** Open the **Capture** project in the Claude app and send `capture this: <link>`, a bare thought, or `idea: <thought>`. You can add a trailing instruction like `tag:urgent` or `file-under:work` — these two are the only instructions this sweep executes automatically (see `dispatch_instruction()`, `harness/skills/memory/scripts/ingest_sweep.py:291-306`); anything else (e.g. "research this further, then file") is left for you to act on yourself, surfaced in the digest rather than executed.

2. **The candidate lands in your vault's `_inbox/`.** The Google Drive connector creates the file directly — your machine can be asleep; Drive delivers it when the machine wakes. Nothing else happens until the ingest sweep runs.

3. **Register the sweep (one-time).**

   ```bash
   cp templates/jobs/capture-ingest-sweep.yaml .harness/jobs/capture-ingest-sweep.yaml
   ```

   `.harness/jobs/` is gitignored, so this is a local, per-machine step. The manifest ships `dry_run: true` — watch a real cycle before flipping it, matching every other new job template in this repo. Until you register it (or flip `dry_run`), run the sweep yourself: `python3 harness/skills/memory/scripts/ingest_sweep.py`.

4. **First cycle: fetch + stage.** Within an hour, the sweep fetches your link (or, for an Obsidian Web Clipper capture, skips the fetch — it already has the full content), and patches your original candidate note in place: `status: ingest_staged`, the fetched text appended under a `## Fetched content` heading. This candidate is not yet recall-visible — it's still sitting in `_inbox/`, exactly as invisible to recall as it was before the sweep touched it (`stage_candidate()`, `ingest_sweep.py:201-247`).

5. **Second cycle: promotion.** Once a staged candidate has survived one full sweep cycle (≈1 hour), the next run promotes it — `ingest.ingest()` (unchanged from `/memory ingest`, see [Ingest an article](Ingest-An-Article)) writes the real, permanent, indexed entries at `personal/domain-reference/`. Your original candidate flips to `status: ingested`. From here it surfaces in ordinary recall like anything else.

6. **A resend isn't fetched or promoted twice.** The Drive connector can create files but never update or delete them, so an uncertain send sometimes lands twice. The sweep checks sibling `_inbox/` candidates for a matching `source_url` before fetching (`_find_duplicate_by_source_url()`, `ingest_sweep.py:177-195`) — a resend is marked `status: ingest_duplicate` and pointed at the original, never re-fetched or promoted separately.

## Why the delay

The first version of this design would have written the fetched content straight to permanent memory, same as an explicit `/memory ingest` call. A pre-merge review on the article-ingestion part (feature 3) found that bypasses this design's own staged/triage trust model for the one path that actually needed it: an automated fetch, with no human reviewing the specific link before it's fetched. The one-cycle staging window keeps the "processed within an hour" promise for the ordinary case, while giving at least one digest cycle where something wrong would be visible before it's trusted. An explicit, human-invoked `/memory ingest` call is unaffected by any of this — you named the source yourself, so it writes directly, same trust level as `memory_append`.

## Verify

- `StagingTests` and `PromotionTests` (`scripts/test_ingest_sweep.py`) prove the full lifecycle both ways: a staged candidate is confirmed recall-invisible (`recall._iter_entry_paths()` returns nothing for it), and the same candidate, after one cycle elapses, is confirmed recall-visible with no special-cased lookup.
- `test_same_cycle_resend_is_not_fetched_or_promoted_twice` proves the duplicate-resend case.
- `ActStepTests` proves the `tag:`/`file-under:` instructions execute, and that anything else — including adversarial strings crafted to look like a command — never executes, only surfaces.

## Troubleshooting

- **Nothing seems to happen.** Check the candidate's own `status:` field in `_inbox/` — `inbox` means the sweep hasn't run yet (or the job isn't registered); `ingest_staged` means it's in its review window; `ingested` means it's done and should be recallable.
- **A fetch failed.** The candidate stays at `status: inbox` with nothing recorded as lost — the sweep's digest surfaces the failure explicitly (`_render_digest()`, `ingest_sweep.py:499-524`). Fix the link or drop the candidate by hand; the sweep will retry it on the next cycle either way.
- **An idea capture (`idea: <thought>`) doesn't show up in `Ideas.md`.** `Ideas.md` lives outside the vault, so folding an idea into it crosses the A3 permeable-write-boundary — which denies by default in this sweep's unattended context. Set `MEMORY_REVIEW_MODE=silent` for the job if you want ideas folded automatically, or fold the candidate yourself via `/memory inbox --bulk-review`.

## See also

- [Ingest an article](Ingest-An-Article) — the explicit, human-invoked door this sweep's promotion step reuses.
- [Memory MCP tools reference](Memory-MCP-Tools) — `memory_capture`'s field-level detail, the tool that puts a candidate in `_inbox/` in the first place.
- `wiki/designs/agentm-capture.md` — the full design, including the Trust Boundary section this page's "Why the delay" summarizes.
