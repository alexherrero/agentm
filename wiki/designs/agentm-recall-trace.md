---
title: recall trace — design
status: launched
kind: design
scope: feature
area: agentm/memory
governs: []
parent: agentm-hld.md
seeded: 2026-07-25
approved: 2026-07-25
---

> [!NOTE]
> **LAUNCHED** (approved 2026-07-25, built same day). Widens the existing
> `recall-history.jsonl` event ledger with the evidence behind each recall
> hit, plus a small reader for it — no new store. Rung 8 of the Loose Ends
> ladder; sibling to [memory index](agentm-memory-index.md). Full build
> narrative in the Amendment log below.

# recall trace

## Objective

When a recalled memory looks wrong — stale, irrelevant, mis-ranked — there is
no way today to ask the system why it surfaced. The evidence exists for one
prompt turn only: `recall.py`'s `_format_recall_result` prints `sim=` and
`keywords=` into the injected block, then it's gone. Auditing a bad recall
means re-running the same query later and hoping the index, the decay clock,
and the vault haven't moved on since. This design persists the evidence at
the moment recall already computes it, so a surfaced memory stays explainable
after the session that surfaced it has ended.

## Overview

`recall_counter.py` already writes one JSONL row per `prompt-submit` recall
call — query hashed, never raw text, plus the slugs that actually survived
truncation. It is the sole per-recall event log in the codebase (`heat_policy`'s
`.heat.json` is a rolled-up per-slug counter, not an event log). This design
widens each row with a `hits` array carrying, per surfaced entry, the score
breakdown `query()` already produces (`sim`, `keyword`, `combined`, rank,
lifecycle tier, decay score) — no new computation, just capturing what already
exists in memory at the existing call site before it is discarded. A new
`memory-recall trace <slug>` subcommand reads the ledger back out,
most-recent-first, for operator or console use.

**The ledger is write-only today.** `record_recall()` has been appending
since 2026-07-13 (1,422 rows as of this drafting), but nothing in production
reads it: `count_since()`'s only caller is its own unit test, and no health
or console surface imports the module. `recall_counter.py`'s docstring says
it exists because "the Morning Brief's 'retrieved' count needs a real
per-recall signal" — that consumer was never wired up. This design does not
fix that gap, and should not be read as fixing it; it is named here because
a design resting on "the ledger is already consumed" would be resting on
something untrue. The trace reader becomes the ledger's first production
reader, which is an argument for the design, not evidence the plumbing
already works.

## Design

### What evidence to capture

`recall.query()` already returns, per hit, a dict with `path`, `slug`, `sim`
(vector cosine-ish score), `keyword` (BM25 score), `combined` (the final RRF
× decay score used for ranking), and — when `lifecycle.py` is importable —
`lifecycle_tier` and `decay_score`. The trace adds exactly one derived field
that doesn't already exist on the dict: `rank`, the hit's 1-indexed position
in the list `query()` returns (already top-k'd and sorted by `combined`).
Rank is read by enumerating `results` itself, not the surviving subset, so an
unreadable-file skip or a token-budget drop never renumbers the hits that
remain. Everything else is a straight carry-over — the trace does not
re-derive or re-score anything.

**Each hit carries `path`, not just `slug`.** Slugs are not unique in a real
vault: the live vault has 6,028 notes with many repeated basenames
(`_index.md`, `_summary.md`, `PLAN.md`, `README.md`, dated daily notes). The
two worst cases are `_index` and `_summary` — precisely the
`_ALTITUDE_ANCHOR_SLUGS` that `query()` rank-boosts, so they are *more* likely
than average to appear in a result set. A trace keyed on slug alone would be
ambiguous exactly where it is most often consulted. The existing `hit_slugs`
field has this same ambiguity; it is left as-is for compatibility, and `hits`
carries the unambiguous vault-relative `path` alongside the slug.

Two fields are deliberately left out rather than invented:

- **Per-stream flags** (did vec vs. BM25 independently produce this hit).
  `sim == 0.0` / `keyword == 0.0` already imply "this stream didn't surface
  it," within the float-boundary caveat that a genuine zero score is
  indistinguishable from absence — the same ambiguity `_format_recall_result`
  already lives with in its own header line. Not worth a second pair of
  fields to resolve a distinction the existing display doesn't resolve either.
- **Altitude-boost flag.** Whether `_ALTITUDE_BOOST` applied is a pure
  function of `slug in _ALTITUDE_ANCHOR_SLUGS` — derivable at read time from
  the stored slug, not worth persisting.

### Where to capture it

`prompt_submit()` is the only call site (matches `recall_counter.record_recall`'s
own single-call-site discipline, stated in its docstring: a lint walk, index
rebuild, or dreaming pass must never reach this). The capture point is right
after token-budget truncation decides `loaded_slugs` — the same point
`record_recall(prompt, loaded_slugs)` already fires from.

Recovering *which* hits survived needs care, and the obvious method is wrong.
`prompt_submit()` builds `raw_blocks`/`raw_slugs` in a loop that `continue`s
past unreadable files, then passes them through `_apply_token_budget()`, which
skips oversized blocks and keeps going. So the surviving `loaded_slugs` is an
order-preserving *subsequence* of `raw_slugs`, which is itself a subsequence
of `results` — and with duplicate slugs in play, filtering `results` by
membership in `loaded_slugs` can match the wrong entry or the same entry
twice.

The fix is to carry the evidence through the budget filter rather than
reconstruct it after: build a `raw_hits` list index-aligned with `raw_blocks`
by construction (appended in the same loop iteration), then pass the slug and
its hit dict through as a single packed pair:

```python
blocks, kept_pairs, omitted = _apply_token_budget(
    raw_blocks, list(zip(raw_slugs, raw_hits)), token_budget
)
loaded_slugs = [s for s, _ in kept_pairs]
kept_hits    = [h for _, h in kept_pairs]
```

This works because `_apply_token_budget` measures only `blocks` — the second
list is zipped along and carried, never inspected — so packing a payload into
it cannot change a keep/skip decision. The alternative (returning kept indices)
would change the helper's return arity at both call sites plus roughly ten
test assertions, including the always-load path `session_start()` shares; this
version leaves the helper's logic and arity untouched and widens only its
`slugs` type annotation from `list[str]` to `list`.

### Row shape

`recall_counter.record_recall`'s signature grows an optional `hits` parameter;
`hit_slugs` (the existing `list[str]`) is unchanged, so `count_since` and any
other reader that only cares about counts keeps working untouched. The new
row shape:

```json
{
  "ts": "...",
  "query_hash": "...",
  "hit_slugs": ["slug-a", "slug-b"],
  "hit_count": 2,
  "hits": [
    {"slug": "slug-a", "path": "personal/slug-a.md", "sim": 0.81,
     "keyword": 12.3, "combined": 0.0163, "rank": 1,
     "lifecycle_tier": "volatile", "decay_score": 0.94},
    {"slug": "slug-b", "path": "projects/agentm/slug-b.md", "sim": 0.0,
     "keyword": 8.1, "combined": 0.0157, "rank": 2}
  ]
}
```

`lifecycle_tier` / `decay_score` are omitted per-hit when `lifecycle.py`
wasn't importable at recall time (mirrors `query()`'s own optional-field
contract on the result dict — no fabricated defaults). Pre-existing rows
written before this change simply lack `hits`; the trace reader treats a
missing key as "this event predates trace capture," not an error or a zero.

### The trace reader

`memory-recall trace <slug> [-n N]` (default `N=3`) returns the N most recent
events whose `hits` contain that slug, printing each one's timestamp, the
matched entry's `path`, and its score breakdown — echoing
`_format_recall_result`'s familiar header shape (`sim=`/`keywords=`/`tier:`),
now durable and queryable outside the session. Scores print at fixed
precision (`sim=%.2f`, `keyword=%.1f`) rather than raw repr; `keyword` is a
BM25 float since V6-3, so unformatted interpolation would print a
twelve-digit tail.

**It reads the whole file and filters, most-recent-first.** A true reverse
streaming read would be the scalable choice, but the ledger does not warrant
it yet and the extra code would be the expensive half of this build: the file
holds 1,422 rows / 167 KB after 13 days (~110 rows/day at ~119 bytes). Adding
`hits` grows a row to roughly 600–700 bytes, so ~70 KB/day, ~25 MB/year. Read
cost stays trivially small on that horizon; `count_since()` already reads the
whole file the same way. **Re-audit trigger:** if the ledger passes ~50 MB, or
a trace call takes longer than a second, switch to a reverse chunked read and
add rotation.

Degrades honestly: a missing ledger file, or a slug that never appears in it,
prints an explicit "never recalled" line rather than staying silent —
matching `console.py`'s honest-dark convention, where a check that has never
run says so instead of vanishing. A slug found in `hit_slugs` but with no
`hits` entry prints "recalled before trace capture landed" — the pre-existing
rows are not retrofittable and must not read as a zero score.

### Console/health surfacing

No new dashboard section. `console.py`'s "Memory activity" block already
shells out to `recall.py`'s subcommands for its rollups; this design adds one
documented pointer there (`memory-recall trace <slug>` as the drill-down for
"why did entry X surface") rather than a new always-rendered row — a per-slug
trace is a drill-down by nature, not a summary metric, and forcing it into
the always-on rollup would just be unread noise on every `/console` run.

## Dependencies

`recall.py` (`query()`'s per-hit dict — the source of every field this design
persists) · `recall_counter.py` (the ledger this design widens) ·
`lifecycle.py` (optional per-hit tier/decay fields, already optional at the
source) · `console.py`'s Memory activity section (gets one doc pointer, no
code dependency).

## Risks & open questions

- **`hits[].path` durably discloses vault directory structure, with no
  redaction tied to a note's later lifecycle.** Found by a pre-tag adversarial
  security review, missed by this design's original risk analysis. `path` is
  a strict superset of what `hit_slugs` already stored (a bare filename stem)
  — it additionally reveals which group/project a note lives under, and nothing
  purges or redacts a ledger row when its source note is later deleted, moved,
  or merged by crystallization. On this operator's actual deployment (a
  single-user local machine where the same OS account already reads both the
  ledger's cache path and the vault itself) this is not a new trust-boundary
  crossing, so it does not block this design — but it is a real, previously-
  unweighed gap, named honestly rather than assumed away, matching this design's
  own "the ledger has no production reader" discipline above. A bare hash (the
  `query_hash` precedent) isn't the fix: it would make `trace()` unable to say
  which entry surfaced, defeating the design's whole purpose. **Re-audit
  trigger:** before this ledger's data ever leaves the single-operator/single-
  machine trust boundary it was built inside — e.g., any future shared,
  synced, or multi-user reader — or if a dedicated rotation/redaction policy
  is designed. Flagged as a separate follow-up, not built here.
- **Ledger has no retention policy.** `recall-history.jsonl` already grows
  unbounded today (pre-existing, not introduced here); widening each row makes
  it grow roughly 5× faster — ~13 KB/day becomes ~70 KB/day. Rotation stays
  out of scope, with the concrete re-audit trigger recorded above (~50 MB, or
  a trace call over a second).
- **The ledger's only production reader will be the one this design adds.**
  Everything the design claims about the ledger's shape is verified against
  `recall_counter.py` itself, not against a downstream consumer's expectations
  — because there is no downstream consumer. If the never-wired Morning Brief
  "retrieved" count is ever built, it should be checked against the widened
  row shape rather than assumed compatible.
- **`hit_slugs` stays ambiguous.** The existing field cannot distinguish two
  entries sharing a basename, and this design does not change it (compatibility
  with `count_since`). The `hits` array is the unambiguous record; anything
  reasoning about *which* entry surfaced must read `hits[].path`, never
  `hit_slugs`.
- **Eval-gate discipline.** This is additive instrumentation at an existing
  call site — it must not touch `query()`'s ranking or `prompt_submit()`'s
  output. `scripts/health/eval_v6_retrieval.py` (the pinned retrieval eval)
  must stay green untouched; if it doesn't, the change has leaked into the
  ranking path and needs to be narrowed back to capture-only. **Verified by
  this design's own build plan's task 7** — see the Amendment log.
- **Partial evidence on budget overrun.** `prompt_submit()`'s own time budget
  can already truncate `results` before `query()` finishes every stream; the
  trace reflects whatever `query()` actually returned in that case — same
  degraded-graceful contract the rest of recall already has, not a new gap.
- **Built** — see the Amendment log for what shipped and where.

## Locked design calls

- **Widen the existing ledger; add no store.** `recall-history.jsonl` is
  already the per-recall event log at the exact call site where the evidence
  exists. A second store would need its own writer, rotation, and reader for
  data one field away from what is already being written.
- **Capture is carry-over only.** Every field except `rank` already exists on
  `query()`'s result dict. The trace must not re-score, re-rank, or re-read
  the vault — if it computes anything, it has become a second ranking
  implementation that can silently disagree with the first.
- **Pack the payload through `_apply_token_budget`; leave the helper's logic
  and arity alone.** It is shared with the always-load path and carries ten-odd
  test assertions. Widening its `slugs` annotation to `list` is the whole
  change.
- **`hits[].path` is the identity; `slug` is a label.** Duplicate basenames are
  real and concentrated in the altitude-boosted anchors.
- **`governs: []` — memory-system keeps governance.** `agentm-memory-system.md`
  already stamps `harness/skills/memory/scripts/`, so `recall.py` and
  `recall_counter.py` are governed. A narrower stamp here would not trip the
  overlap gate (different pattern string) but would quietly redirect `/plan`
  and `/review` resolution for those files to a thin sub-slice design. Not
  worth it for one instrumentation change.
- **No new console section.** A per-slug trace is a drill-down; the Memory
  activity block gets a documented pointer, not an always-rendered row.

## References

- `harness/skills/memory/scripts/recall.py` — `query()` (the per-hit dict this
  design persists verbatim), `prompt_submit()` (the capture call site),
  `_format_recall_result()` (the header shape the trace reader echoes)
- `harness/skills/memory/scripts/recall_counter.py` — `record_recall()` /
  `count_since()` (the ledger this design widens, additively)
- `harness/skills/memory/scripts/heat_policy.py` — the rolled-up per-slug
  counter this design deliberately does NOT duplicate (different shape,
  different purpose)
- `harness/skills/console/scripts/console.py` — the Memory activity section
  gaining the doc pointer
- [memory index](agentm-memory-index.md) · [memory system](agentm-memory-system.md) —
  sibling designs `query()`'s hybrid-recall + lifecycle machinery live under
- `<vault>/projects/agentm/_harness/designs/loose-ends/SWEEP-INVENTORY.md` —
  the row this design resolves ("Recall-trace substrate")

## Amendment log

*Newest first.*

**2026-07-25 — Built, same day as approval.** All four Design subsections
shipped exactly as locked, no deviation found worth a NOTE:

- `recall_counter.record_recall()` gains the optional `hits` param
  (`harness/skills/memory/scripts/recall_counter.py:32`) — omitted by
  default writes today's exact row shape (no `hits` key at all, not `[]`).
- `recall.py`'s `prompt_submit()` packs `(slug, hit)` pairs through
  `_apply_token_budget` (`harness/skills/memory/scripts/recall.py:1561`
  builds `raw_hits`, `:1586` unpacks `kept_hits`, `:1595` passes them to
  `record_recall`) — `_apply_token_budget`'s logic and arity are untouched,
  only its `slugs` parameter's type annotation widened. `rank` is read off
  `results`' own `enumerate()`, before the read-loop's unreadable-file skip
  can renumber it — proven necessary and correct by a duplicate-slug test
  (two entries sharing a slug, different paths) that fails under the naive
  "filter `results` by `loaded_slugs` membership" approach this design
  itself rejected below.
- The reader lands as `recall.trace()` (`harness/skills/memory/scripts/recall.py:1356`)
  + the `memory-recall trace <slug> [-n N]` CLI subcommand — a sized
  whole-file read (not the reverse-chunked scan an earlier draft wrongly
  promised), all three degrade cases each an explicit printed line.
- `console.py`'s Memory Activity section gets its one documented pointer
  (`harness/skills/console/scripts/console.py:535`), not a new row.

19 new tests across three files (4 in `test_recall_counter.py`, 9 in the new
`test_recall_trace.py`, 1 in `test_console.py`), plus manual smoke-testing
against this machine's real 1,400+-row production ledger — which surfaced a
genuine pre-existing bug, unrelated to this design: `test_recall_token_budget.py`'s
`prompt_submit()` integration tests don't mock `recall_counter`, so they've
been writing test rows into the real ledger. Flagged as a separate follow-up,
not fixed here (out of this design's scope).

**Eval-gate outcome (task 7).** `bash scripts/check-all.sh` — 34/34 green,
including the full `unittest discover` sweep (which already exercises
`scripts/health/test_eval_v6_retrieval.py`'s own fixture-based suite). The
CLI (`python3 scripts/health/eval_v6_retrieval.py`) run directly against the
real vault exits 1, but on a cause unrelated to this design: 4 of its
pinned query set's expected-notes paths have moved or been archived since
that query set was authored — the intentional fail-loud-on-drift behavior
v9.2.1 shipped, refusing to report a number it can't trust rather than
silently under-counting. This diff cannot be the cause (it touches zero
vault content), and the drift predates it. Flagged separately, not fixed
here. In its place: `query()` — the function that does every bit of
scoring, ranking, RRF fusion, and decay/lifecycle weighting — is byte-for-
byte untouched by this diff (confirmed directly against the base commit,
not merely asserted); `_apply_token_budget`'s only change is a type
annotation plus docstring text, zero logic lines. Ranking cannot have
regressed by construction, independent of the live-vault eval's drift
issue.

**2026-07-25** — Initial draft, authored per the Loose Ends ladder's rung 8
(conditional item, handed to a dedicated session). Grounded against `recall.py`
/ `recall_counter.py` / `vec_index.py` / `console.py` as they exist today, then
corrected on a verification pass that caught four things the first draft had
wrong: the ledger has no production reader (the design had implied one), slugs
are not unique so `hits` must carry `path`, filtering `results` by
`loaded_slugs` mis-identifies entries under duplicate basenames, and the
promised "backward scan" would not have been implemented as described. Area
corrected `agentm/recall-trace` → `agentm/memory` (the former is not in the
taxonomy `check-governs-index` enforces, so it would have failed the gate) and
the page linked into `designs/_Sidebar.md` + `Designs.md` (`check-wiki`'s
no-orphan rule).
