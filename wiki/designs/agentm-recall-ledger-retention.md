---
title: recall ledger retention — design
status: launched
kind: design
scope: feature
area: agentm/memory
governs: []
parent: agentm-hld.md
seeded: 2026-07-26
approved: 2026-07-26
---

> [!NOTE]
> **LAUNCHED** (built 2026-07-26, same session as authoring). Closes the
> retention gap [recall trace](agentm-recall-trace.md) named in its own Risks
> section. Widens no store and adds no scheduler — recall evidence expires on
> a clock, swept from the write path that already runs. Build narrative in the
> Amendment log below.

# recall ledger retention

## Objective

`recall-history.jsonl` keeps every recall event forever. Since [recall trace](agentm-recall-trace.md)
widened each row with `hits[].path`, those rows now name the vault directory a
note lived in, and nothing ever removes one — a path stays on disk long after
the note it describes is deleted, demoted, or archived. The pre-tag security
review named this and accepted it as low-severity for now, on the condition
that a real retention policy follow. This is that policy: recall evidence
expires on a clock, so what the ledger discloses is bounded to the window
where it is still useful.

Measurement reframes which half is urgent. At this machine's observed volume
the file needs *years* to reach the ~50 MB re-audit threshold the trace design
named, so unbounded growth is not the pressing problem. The pressing problem
is that disclosure has no expiry at all.

## Overview

Retention is by **age**, enforced at the **write site**, with a size ceiling as
a backstop.

`recall_counter.record_recall()` — which already fires on every prompt-submit —
gains a cheap check after its append: is the ledger's oldest row past the
retention window, or has the file crossed a size ceiling? When either trips, it
rewrites the ledger keeping only rows inside the window. No new scheduler, no
new store, no new file format. Readers (`trace()`, `count_since()`) are
untouched: row shape does not change, and both already read the whole file and
skip anything they can't parse.

The insight that makes this simple is that **age subsumes redaction**. An old
row is both the growth problem and the stale-disclosure problem at once. A row
naming a note deleted six months ago disappears because the row is six months
old — no vault lookup, no chasing a moving target, and recent deletions stay
traceable, which is exactly when you want them.

## Design

### Policy: expire by age, ceiling by size

Two module constants in `recall_counter.py`:

- `RETENTION_DAYS = 90.0` — rows older than this are dropped.
- `MAX_BYTES = 25 * 1024 * 1024` — a hard ceiling that prunes regardless of age.

90 days is chosen against what the reader actually does. `trace()` defaults to
the 3 most recent events, and the trace design's own opening argues that
auditing an older recall means hoping the index, the decay clock, and the vault
have not moved on since. A quarter comfortably spans "did recall regress after
the change I made last month" while bounding how long a path lingers. Evidence
past that point is unusable for the question the feature exists to answer.

The ceiling sits below the trace design's stated ~50 MB re-audit trigger on
purpose: enforcement should engage *before* the threshold that says "go redesign
this," not after. Measured against both sizing estimates it is a genuine
backstop rather than the common path — see the growth numbers below.

### Trigger: an O(1) probe on the path that provably runs

The ledger is append-only and chronological, so **line 1 is the oldest row**.
Reading just that line and parsing its `ts` answers "is anything past the
window?" without reading the file.

Measured on the real 185 KB production ledger: `os.stat` plus a first-line read
costs **11.9 µs**, and a full read — what the prune itself pays — costs
**0.12 ms**. Both vanish inside `prompt_submit()`, which already runs vector
search, BM25, and a file read per hit. The check is unconditional; there is
nothing to gain by gating something this cheap.

An unparseable first line counts as "prune." It is torn-write debris, the prune
drops unreadable rows, and the state self-heals in a single pass — which also
avoids a stuck ledger that could never age-prune because its head row was
garbage.

**Why the write site and not a scheduled sweep.** This is the load-bearing call.
`dream.py`'s job template is weekly, ships `dry_run: true`, and is not seeded at
install — it runs only if the operator wired it up by hand. Worse,
`dream_confirm.cleanup_applied_batches()`, the one retention routine this repo
already has, **has no production caller at all**: grep finds only its own tests.
A policy hung off a scheduler that may never run is a policy that silently is
not enforced, and this repo has shipped that failure twice already — session
reflection sat dead for 57 days under green CI, and the crystallization trigger
shipped inert. `record_recall()` is the one code path that provably executes,
because it is the path that creates the rows in the first place. Putting the
enforcement there means it cannot ship dead.

### Why not purge rows whose note is gone

Liveness-based redaction — stat each `hits[].path` and drop rows whose file has
vanished — reads well and is wrong in three ways the code makes concrete:

- **Moves outnumber deletions, and nothing records them.**
  `heat_policy.run_policy()` relocates notes between `personal/` and
  `personal/_always-load/` on promote and demote (`heat_policy.py:312`, `:364`,
  `:417` — each an `unlink` after writing the new location), and
  `dream._stage_tidying` moves notes into archive and shelf tiers
  (`dream.py:590`, `:605`, `:621`). Neither records an old→new mapping. A
  liveness sweep sees "path no longer resolves" for a note that is alive and
  well one directory over, and deletes its history — losing trace data for
  precisely the notes the system is actively reorganizing.
- **The vault's invariant is never-delete, so there is little to collect.**
  `dream.py:433-436` states it outright and marks `compacted_into` instead of
  removing a file; dedup marks `superseded` and leaves both notes in place.
  `crystallize.py` deletes only staging markers (`crystallize.py:215`), never a
  note — so the "merged by crystallization" case this was meant to cover does
  not exist.
- **Deletion is when the trace matters most.** A note deleted *because* it kept
  surfacing wrongly is the highest-value thing to ask "why did this surface?"
  about. Purging on delete destroys the evidence at the moment it becomes
  interesting.

Age-based expiry gets the same disclosure bound without resolving anything
against a vault that moves underneath it.

### Not reverting `path` to a slug

Unchanged from the trace design's locked call: hashing or truncating `path`
back to a bare slug would leave `trace()` unable to say which of several
same-named entries actually surfaced — and duplicate basenames concentrate in
the `_index` / `_summary` anchors that recall rank-boosts. That defeats the
feature. Retention shortens how long the path is kept; it does not make the
path ambiguous while it is kept.

### Prune mechanics and concurrency

`prune_history()` reads the ledger, keeps rows whose `ts` is inside the window,
and writes the survivors through `vault_lock.atomic_write` — the repo's
canonical writer (temp file with a per-writer-unique name, fsync, then
`os.replace`). It returns `{"kept": N, "dropped": N}` so tests can assert on
real counts and a future surface can report them.

Rows that cannot be parsed, or that carry no usable `ts`, are **dropped**. No
reader can interpret them — `trace()` and `count_since()` both already skip such
lines — and keeping them means torn-write debris accumulates with no path to
removal.

Appends stay lock-free, exactly as today. **A row appended between the prune's
read and its rename is lost**, because it lands on the inode the rename
replaces. Stated plainly rather than papered over: the window is roughly
0.1 ms, prunes fire on the order of monthly at observed volume, and the ledger
is already explicitly best-effort — `record_recall` swallows `OSError` and
returns. Losing a few telemetry rows on that cadence is proportionate to what
this file is.

No mutex is taken, and that is deliberate rather than an omission. A lock would
only serialize prune against prune, and two concurrent prunes each write a
complete valid file through `atomic_write`, so one simply wins — there is no
corruption to prevent. It would not help the append case at all, since appenders
do not take it, and making every prompt-submit acquire a lock to protect a
monthly rewrite is the wrong trade.

### Test isolation, and why an escape hatch still earns its place

`recall.py:1614` calls `record_recall` with no `history_path`, so anything
exercising `prompt_submit()` writes the real ledger. Three suites did, and the
result is visible in production: the operator's ledger holds
`personal/recall-entry-00.md`, `recall-entry-01.md`, and `test-convention.md` —
test fixtures, not real notes.

The trace design flagged this as a benign follow-up, and while the ledger was
append-only it was. Pruning changes that: an unmocked caller no longer just adds
a row, it read-modify-writes the file and can drop real ones.

**PR #390 fixed it independently, before this branch, by mocking
`record_recall` in all three suites.** That mocking is the primary guard and
this design does not duplicate it. What lands here is the layer underneath:
`default_history_path()` honors an `AGENTM_RECALL_HISTORY` override (mirroring
`MEMORY_VAULT_PATH`, `AGENTM_TELEMETRY_DIR`, and `XDG_CACHE_HOME`). Mocking
protects the call sites someone remembered to mock; redirecting the path
protects the ones nobody did. Cheap, and it is also the seam this design's own
tests use.

### Growth, measured

Taken from the live ledger (1,508 rows spanning 13.0 days):

| Quantity | Observed |
|---|---|
| Recall events per day | 116 |
| Legacy row (no `hits`) | 120 bytes |
| Cost per captured hit | ~224 bytes |
| Mean hits per recall | 0.58 |
| Projected widened row | ~250 bytes |
| **Projected growth** | **~28 KB/day** |

That is below the trace design's ~70 KB/day estimate, which assumed a fuller
result set. Both land in the same place: 90 days of retention holds **2.5 MB**
at the measured rate, **6.3 MB** at the design's more pessimistic one. Against a
25 MB ceiling, the size backstop should never fire in normal operation — it
exists for a burst that fills the window early, which is what a backstop is for.

The mean-hits figure is measured against a ledger polluted by the test rows
above, so treat it as a floor rather than a settled number; the conclusion holds
under either estimate, which is why it is not worth re-deriving after the
isolation fix lands.

## Dependencies

`recall_counter.py` (the ledger and its writer — the whole surface this design
changes) · `vault_lock.atomic_write` (the canonical writer the prune's rewrite
goes through) · `recall.py`'s `prompt_submit()` (the call site that triggers the
check, and the integration tests that must stop writing the real ledger) ·
[recall trace](agentm-recall-trace.md) (the design whose named gap this closes).

## Risks & open questions

- **A concurrent append during a prune is lost.** Accepted, with the reasoning
  above: rare, sub-millisecond, on a best-effort ledger. **Re-audit trigger:** if
  the ledger ever becomes an input to billing, health scoring, or anything where
  a missing row changes a reported number rather than thinning telemetry.
- **90 days is a judgment call, not a derived constant.** It is anchored to
  `trace()`'s 3-event default and the argument that older evidence is unusable,
  not to a measured point where operators stop asking. **Re-audit trigger:** a
  real trace query that wanted an event the window had already dropped.
- **The trust boundary is unchanged.** Retention bounds how *long* vault
  structure is disclosed; it does not change *who* can read it. The trace
  design's original trigger stands untouched: re-audit before this ledger's data
  leaves the single-operator, single-machine boundary it was built inside — any
  shared, synced, or multi-user reader.
- **The size ceiling is a second trigger for the same policy, and could
  disagree with it.** At a burst rate high enough to hit 25 MB inside 90 days,
  effective retention becomes shorter than the stated window. That is the
  intended precedence — the ceiling wins — but it means "90 days" is a ceiling
  on age, not a guarantee of it.
- **First-line-is-oldest assumes roughly monotonic timestamps.** Concurrent
  sessions can interleave appends by milliseconds, and a backward clock jump
  could reorder rows. Both are negligible against a 90-day window; the effect is
  a prune firing marginally early or late, never a wrong row surviving.
- **Nothing reports what a prune dropped.** `prune_history()` returns counts,
  but no surface displays them, so retention runs silently. Deliberate for now —
  `console.py` gained a pointer rather than a row for the trace reader itself,
  on the same "drill-down, not summary metric" reasoning. **Re-audit trigger:**
  the first time someone asks why a trace came back empty.

## References

- `harness/skills/memory/scripts/recall_counter.py` — `record_recall()` /
  `count_since()` / `default_history_path()`; the ledger this design bounds
- `harness/skills/memory/scripts/recall.py:1614` — the unconditioned
  `record_recall` call this design isolates for tests; `trace()` at `:1356`
- `harness/skills/memory/scripts/vault_lock.py` — `atomic_write`, the canonical
  writer the prune rewrites through
- `harness/skills/memory/scripts/heat_policy.py:312,364,417` ·
  `harness/skills/memory/scripts/dream.py:433-436,590,605,621` ·
  `harness/skills/memory/scripts/crystallize.py:215` — the move-and-never-delete
  behavior that rules out liveness-based redaction
- `harness/skills/memory/scripts/dream_confirm.py:804` —
  `cleanup_applied_batches()`, the retention routine with no production caller
- [recall trace](agentm-recall-trace.md) · [memory system](agentm-memory-system.md)

## Amendment log

*Newest first.*

**2026-08-01 — Rebased onto v9.5.0; isolation credit corrected.** Main gained
[PR #390](https://github.com/alexherrero/agentm/pull/390) while this branch was
open, fixing the same three polluting suites by mocking `record_recall`. That
work landed first and stands; this branch's duplicate per-test redirection was
dropped in the rebase rather than layered on top of it. The
`AGENTM_RECALL_HISTORY` override stays, re-framed from "the fix" to the escape
hatch under the mocks — see the Design section above. The earlier entry's claim
to have found and fixed the isolation bug is corrected here: it was found
independently on both branches, and #390 got there first.

**2026-07-26 — Built, same session as authoring.** Everything in Design
shipped as written.

- `recall_counter.py` gains `RETENTION_DAYS = 90.0` / `MAX_BYTES = 25 MB`,
  `prune_history()`, the O(1) `_needs_prune()` probe, the `_row_ts()` parser
  that tolerates every shape a corrupt line can take, and `_maybe_prune()`
  wired into `record_recall()`'s `else` branch — so a failed append never
  triggers a sweep, and a failed sweep never reaches the recall pipeline.
- `default_history_path()` honors `$AGENTM_RECALL_HISTORY`. Three suites drove
  `prompt_submit()` unmocked where the trace design had flagged one; both this
  branch and [PR #390](https://github.com/alexherrero/agentm/pull/390) found
  that independently, and #390's mocking landed first (see the 2026-08-01 entry
  above). Verified at the time by running all four `prompt_submit` suites (67
  tests) against a byte-counted real ledger: unchanged.
- 21 new tests. Retention boundaries are hand-computed dates against a fixed
  anchor (NOW = 2026-07-26, cutoff = 2026-04-27, arithmetic shown in the test
  file) rather than a cutoff re-derived from `RETENTION_DAYS`, which would only
  prove the test and the implementation agree.

**Verification.** `bash scripts/check-all.sh` — 34/34 green. The suite was then
mutation-tested rather than trusted: widening the window to 9000 days (6
failures), disabling the age trigger (3), and keeping unparseable rows (1) each
turn it red. A fourth mutation — re-serializing survivors instead of re-emitting
them — initially passed, because the fixture was already in
`json.dumps(sort_keys=True)` form and round-tripped unchanged; the test now uses
a deliberately non-canonical row and catches it. Smoke-tested against a copy of
the real 1,509-row production ledger: a 7-day window kept 616 and dropped 893 in
3.2 ms, oldest survivor landing exactly on the boundary, `kept + dropped`
reconciling to the original count, and `trace()` still reading the swept file
including its never-recalled degrade path.

**One finding worth recording.** A third of the operator's live ledger — 492 of
1,509 rows — is test-fixture pollution from those unmocked suites. Retention
will not clear it, since the rows are recent rather than expired; it ages out
on the normal 90-day clock like anything else. Left alone deliberately rather
than hand-scrubbing the operator's telemetry.

**2026-07-26** — Initial draft. Authored against the gap
[recall trace](agentm-recall-trace.md) named in its own Risks section and left
as an explicit follow-up. Grounded by reading `recall_counter.py`, `recall.py`,
`heat_policy.py`, `dream.py`, `crystallize.py`, and `dream_confirm.py` as they
exist today, plus direct measurement of the live production ledger rather than
the trace design's estimates.

Four findings changed the shape from what the follow-up brief anticipated. The
brief's "merged by `crystallize.py`" case does not exist — crystallization
deletes staging markers only, and dream's dedup and compression stages mark
`superseded` / `compacted_into` under a stated never-delete-sources invariant.
Liveness-based redaction is therefore actively harmful, because `heat_policy`
and `_stage_tidying` *move* notes without recording old→new, so the sweep would
delete history for notes that are alive one directory over. Growth turned out
to be the less urgent half: measured volume puts the
50 MB re-audit trigger years away, so the policy is shaped around bounding
disclosure. And the test-pollution bug the trace design deferred as benign
becomes destructive the moment `record_recall` can rewrite the file, which
promotes test isolation from follow-up to prerequisite.
