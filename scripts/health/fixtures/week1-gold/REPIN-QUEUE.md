# Re-pin queue

What the next re-pin of `shipped-baseline.json` has to decide, collected so
the decision starts from evidence instead of from a fresh investigation. The
standing rule (the operator's): no re-pin until the vault series closes and
several perfection passes have run. Until then, every question the gate misses
against the 2026-09-19 baseline is listed here with its cause.

Written at the close of task 182 (2026-10-02, #797), after the defects that
nightly passes were writing into the vault were fixed. Each item names what is
true now, the evidence, and the choice the re-pin faces.

## 2026-11-21: the split re-pin, and what this queue holds now

The operator ruled on 2026-11-21 to split the gold set into gate and report
questions and re-pin, as an exception to the standing rule (see
BASELINE-HISTORY.md). The entries below that are against the 2026-09-19
baseline are kept as the record of that pin. From this pin on, this queue
holds one kind of entry: a gate question the eval names on a `left the gate:`
line, because one of its notes has since been demoted on purpose. The next
re-pin moves each one to the report, or drops the demoted note from its
`gate_note_paths`.

**Why the 2026-09-19 pin went red on 2026-11-21 (R@5 0.667 → 0.533, 2 hits
gained against 11 lost, p 0.0225).** The flip split was 3 vs 5 every night up
to 2026-11-03 and 3 vs 9 on 2026-11-04. The jump lines up with vault commits
`9f1a91c1b`, `46c27653a` and `c4c586097`, written between 22:41 and 22:45 on
2026-11-03: the content review moving finished agentm designs and the whole of
primos into `completed/`. Of the eleven misses:

- **Seven expect notes now under `completed/`.** These rank at ×0.30
  (`archive-class`): dt04 (rank 2 → 99), dt05 (2 → 19), dt08 (1 → 18),
  dt10 (4 → 7), pp01 (2 → 36 and 57), pp08 (2 → 12 and 26), and one of
  ep10's two notes. All are report questions now, except ep10, which gates on
  its Sherwood decisions note alone.
- **ep02 expects Shrimpi notes.** On 2026-11-20 (#972) Shrimpi was walled to
  `/shrimpi-mono/`. Asked from a folder of that name, its notes rank 1, 3
  and 4; from the gate's neutral folder they never come back. This is the
  11-20 → 11-21 flip. It is a report question now.
- **ep06 and ep10 were already queued here** in October.
- **ep03, rd08 and ep01 were displaced just past rank 5 by newer notes**
  (blog tasks 016 and 017, `mocs/needs-review.md`, home-tech-next's own
  VPN research). ep03 is a report question (an open task); rd08 and ep01 gate,
  and miss at the new pin.

Ruled out: stale vectors (after `agentmd embed` left 0 stale, the reading was
0.517 — ep01 slipped one place), the space maps of #978 and #979 (the 11-04
step came first), and any ranker deploy.

## Final measurement (task 182 close)

Live vault, deployed `f26955f6`, after `agentmd embed` (0 stale), 2026-10-02:
R@5 **0.651** (41 of 63), R@1 0.365, against the baseline's 0.667. The gate is
clean (exact paired p 1.0). Three questions flipped to a hit (`rc06`, `rc10`,
`rd04`) and four to a miss (`dt06`, `ep06`, `ep10`, `pp02`, below). The
canary answered at rank 1. The corpus moved from 2,883 documents at the pin to
4,081.

For the trail: R@5 was 0.540 when #749 was filed (2026-09-29), 0.635 after
#776 and Plan D (2026-10-01), 0.619 at task 182's first deploy, and 0.651 now.

## Questions that miss against the baseline

### `dt06` — where primos is filed

- **Expected:** `projects/primos/charter.md` (the gold set's
  `Agent/external/primos/_index.md`, remapped).
- **Why it misses:** primos has had no work since 2026-06-09, so its records
  rank at ×0.5. That is the activity band the design specifies, read correctly
  since task 182 step 4 (the operator's ruling that only work counts). Before
  then, a generated `blueprint.md` dated 2026-09-25 made primos read as active.
  On top of the band, the primos repo and issue entity pages (Plan D's
  exact-name rule) take ranks 1 and 2.
- **For the re-pin:** this miss is designed behaviour. Either accept it, or
  give the charter `importance` (the design's mitigation for a live project
  ranking below a done one).

### `ep06` — when dev-setup was last worked

- **Expected:** `projects/dev-setup/desk/progress.md` and the 2026-06-26
  ecosystem-reconciliation design.
- **Why it misses:** entity pages crowd the top five. The dev-setup repo page
  is first by the exact-name rule, and release pages for v2, v3 and v4 sit
  near it. The review showed that even with no entity pages, the expected
  design ranks 6th, behind the generated project-root files (moc, tracker,
  charter, blueprint).
- **Evidence the gold set may be stale:** the project tracker body says
  "closed 2026-06-26", the same answer the gold set expects. The real latest
  dev-setup work, commit 013a911 on 2026-09-29, is in no vault note.
- **For the re-pin:** consider accepting the tracker as an answer, and
  revisit Plan D's one-month review of the exact-name rule for questions
  about time ("when") rather than identity.

### `ep10` — when sherwood was first rewritten

- **Expected:** `projects/blog/drafts/_archive/rebuild-not-refactor.md` and
  `projects/sherwood/decisions/2026-04-27-v2-design-decisions.md`.
- **Why it misses:** the decisions doc fell from rank 2 at the pin to 6.
  Three newer notes now outrank it: the sherwood entity page (2026-09-30),
  the generated `blueprint.md` (2026-09-25) and `docs/conventions.md` (moved
  2026-09-25).
- **Not a survivor:** rank 1 is `projects/blog/drafts/_rewrite/rebuild-not-refactor.md`,
  a later revision of the expected essay (created 2026-08-25, after the gold
  corpus of 2026-08-11). The archived original was never merged into it, so
  task 182 added no successor row; accepting it is a relabel.
- **For the re-pin:** decide whether the live revision answers for the
  archived draft.

### `pp02` — where the worktree rules are stored

- **Expected:** `agent/memory/semantic/worktrees-never-auto.md`.
- **Why it misses:** the card is rightly stamped under the lesson
  `harness-project-json` (the recheck kept it), and that lesson sits inside
  the arm's top 50, so the card ranks at ×0.30 (around rank 9). The lesson
  answers the question well: `.harness/project.json` holds `isolation.mode`,
  the per-repo deviation from never-auto-spawn. But it ranks about 50th,
  because its wording differs from the question's.
- **For the re-pin:** the lesson is a legitimate successor (true of the card
  and answering the question). A `_SUCCESSORS` row would count it, but only
  once it ranks in the top five, so the real lever is the lesson's own
  recall.

## Questions that recovered

- **`rc02`, `rc11`, `rc06`.** `rc02` came back when its card's capture-time
  aliases were restored (step 5). `rc11` came back once the consolidated
  demotion applied only beside a lesson in the arm's top k (step 3). `rc06`
  recovered alongside them.
- **`rc10`, `rd04`.** These flipped to hits after the pin, from corpus
  changes.
- **`pp10`.** Its `_SUCCESSORS` row names `context-is-ephemeral-files-are-durable`.
  The recheck released the card from that lesson, so the row has revoked
  itself as designed. The card still answers on its own.

## The hard negatives

The near-miss negatives that serve their banned note went from 9 of 10 at the
pin to 4 of 10. That line is going quiet as the vault reorganizes:

- `ngh07`–`ngh10`: the banned notes are task plans that moved to
  `completed/tasks/` and now take the archive-class ×0.30 (`ngh07` and `ngh10`
  ban the same note).
- `ngh06`: the banned scorecard sits in `agent/diagnostics/`, a dampened space.
- `ngh02`: already quiet at the pin.

Re-authoring the negatives changes the gold set's hash, so it belongs to the
re-pin.

## Related backlog

- **Query term selection.** The hook's term cap keeps low-information words
  and drops the distinctive ones; `rc02`'s question lost "notes" and "timer".
  This is its own pre-registered experiment, scored on the full gold set
  (board backlog item `b-query-term-selection-drops-distinctive-words`).
- **The Python recall arm applies no project-activity band.** This was a
  parity gap before task 182; it is noted here and was not widened.
