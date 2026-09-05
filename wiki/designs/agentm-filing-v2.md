---
title: AgentM Filing v2
status: launched
visibility: published
kind: design
scope: arc
area: agentm/vault
author: alexherrero
contributors: []
created: 2026-09-01
updated: 2026-09-05
last_major_revision: 2026-09-01
prd:
project:
---

# AgentM Filing v2

## Context

### Objective

The vault hybridizes several generations of filing systems, and the six-class
memory layout that shipped in August was never populated — the corpus sits in
a staging inbox whose routing step was never built. This design converges the
whole vault on the layout the operator's work deployment has proven, defines
who may write where, and adds the two subsystems neither deployment has
solved: a memory lifecycle and a consolidation pass with authority to correct
filing. It is the blueprint for AgentM as a machine-level harness rather than
a repo harness.

### Background

The predecessor design, [AgentM Rescope — The Filing Contract]
(agentm-rescope-filing.md), went final on 2026-08-18 and shipped its first
two parts: `standards/storage-rules.md` as the runtime-read source of truth,
the six-value type taxonomy with its warrant gate, and the type-XOR-kind
contract. A census taken 2026-08-31 (`INVENTORY-home-vault.md` in the
research bundle) found what happened next: the six class directories each
contain only their own index file, while the real corpus — 2,652 notes —
accumulated in `memory/_inbox/`, 61% of it expired output from the #514
auto-mining defect. The capture side assigns types correctly; nothing ever
routed a note onward, because `reflect.py`'s only destination is the inbox.
Around that stalled core sit the older strata: legacy kind-named directories,
a date-bucketed directory, and roughly ten underscore machinery directories.

Two things changed the picture in late August. The operator's work
deployment — AgentKV, a sibling of the same lineage, deployed fresh in
mid-August — demonstrated the target shape working without any of the
accumulated machinery: an `agent/` folder with exactly three children,
first-class diagnostics with daily scorecards, an agent-maintained faceted
calendar, and no staging directories at all, their jobs carried by a
dedicated dreaming binary running beside the MCP daemon. And a six-lane
evidence review (R1–R6, `desk/projects/agentm/_harness/research-filing-v2/`)
plus the census established with unusual consistency what to keep, what to
change, and where the real risks are. The operator then ruled on every open
question in conversation on 2026-08-31 and 2026-09-01; those rulings are
recorded in the research bundle's synthesis and are treated as decided
throughout this design.

The environment constrains the shape. One git repository at `~/Vault` is
browsed by the operator in Obsidian, synced through
Google Drive, and served to the agent by a Go daemon (`agentmd`) doing
hybrid lexical+dense retrieval, with hooks providing recall and reflection
at session boundaries. The live memory corpus is on the order of 1,000–1,500
notes once scratch, expired mining output, and the operator's personal
content are subtracted — so most of v2 is edits to a rules file that already
runs, a scripted one-time migration, and population of an existing
structure, not a rebuild.

## Design

### Overview

The vault keeps its five top-level spaces — `Agent/`, `Calendar/`,
`Projects/`, `Personal/`, `standards/` — and each space gets an explicit
write-authority level, replacing the old promotion-door doctrine. The
agent's half trims to exactly three children: `desk/` (one directory per
in-flight task), `diagnostics/` (per-system health with daily scorecards and
dated audits), and `memory/` (the six classes, and nothing else). Memory
type stays a frontmatter field; the class directory stays the only path
axis; records keep `kind:` and live in the area they serve.

Three subsystems are new. Filing moves to write time: a hook classifies each
memory to its final destination and decides how it relates to what already
exists — add, update, supersede, or nothing — with no staging inbox; the one
useful property of the old queue survives as a confidence flag in
frontmatter. A graduated lifecycle status replaces archive-by-moving:
demotion is a frontmatter edit read by the retrieval daemon as a ranking
curve, and nothing in the memory classes ever relocates. And the calendar
becomes an agent-maintained daily register with four facets and a generated
day index, append-only, with corrections as linked supersessions.

Underneath, the machinery consolidates: the Python dreaming layer ports to a
second Go binary with real mutation authority over prior filings, all engine
state (caches, cursors, journals) leaves the vault for
`~/.local/state/agentm/`, and the vault becomes purely the knowledge surface
both audiences read.

### Infrastructure

Everything runs on the operator's Mac under launchd; no servers, no cloud
components.

| component | role | status in v2 |
|---|---|---|
| `agentmd` (Go daemon) | index, hybrid retrieval, MCP surface, watch, rules holder | extended: lifecycle-status ranking, write-authority validation, filing endpoint |
| dreaming binary (Go) | nightly consolidation with mutation authority; rollups; audits | new — ports `dream.py` / `crystallize.py` / `reflect.py` and kin |
| `llama-server` | dense embeddings (embeddinggemma-300M) | unchanged |
| session hooks | recall (SessionStart, UserPromptSubmit), capture/reflection (Stop) | capture hook rewired from inbox-writes to the filing decision |
| gate battery (`check-all.sh`) | deterministic verification | extended: vocabulary, provenance, lifecycle gates |

| trigger | what runs |
|---|---|
| session start / prompt submit | recall injection from the daemon |
| session stop | capture → write-time filing decision |
| nightly (gated on elapsed time and activity, lock-coordinated, exits when done) | the dreaming binary: mutation pass, promotion, MOC regeneration, rollups, audits |
| daily | per-system scorecards into `Agent/diagnostics/` |
| every push / CI | the gate battery |

Guarantees carried over and added: a storage-rules block that fails to parse
halts filing loudly (fail-closed); authoritative state lives on disk always —
the dreaming binary checkpoints its in-flight work as journaled, lock-guarded
files under `~/.local/state/agentm/`, so a crash mid-consolidation is
recoverable rather than silently lossy; and no same-turn read-after-write
expectation is placed on the index.

### Detailed Design

#### Rules, vocabulary, and write authority

`standards/storage-rules.md` remains the single runtime-read source of
truth, and most of this part is an edit to it. The block gains: a
`lifecycle` enum (`pinned · active · dormant · archived · superseded`,
retiring `expired`); required provenance fields (`source:` with a closed
transport vocabulary — operator-direct, conversation, external-fetch, email;
external sources carry lower default trust); `filing_confidence` on
auto-filed notes; the calendar facet registry (`meetings · correspondence ·
docs · diary`); and a routing change — `idea` routes to `memory/semantic`
instead of `desk`, with the idea-incubator's read path repointed.

The vocabularies get the gate the census showed missing: enum membership is
enforced for both `type:` and `kind:` (the XOR gate held at zero violations
while `kind:` drifted to 38 values against a 32 register), and a collision
check refuses any word legal in both vocabularies, renaming the record kinds
that currently shadow memory types. The warrant rule stays, paired with a
scheduled vocabulary audit in the dreaming cadence — ungoverned metadata
axes decay by default, and the 55-value history is this vault's own proof.

Write authority replaces the promotion door as one table the validator (the
daemon's `door` package, generalized) enforces:

| space | authority |
|---|---|
| `Agent/` | agent-authored freely |
| `Calendar/` | agent-maintained; operator co-writes |
| `Projects/` | agent-managed under a session grant — "open the files for project `<slug>`" grants that project, that session; otherwise read-only |
| `Personal/` | explicit per-task instruction only; never standing management |
| `standards/`, root files | operator-owned; agent proposes, applies only on instruction |

Validators are scoped so `Personal/` is exempt from the memory contract
entirely — its notes carry no required frontmatter. `Filing.md` and
`index.md` are rewritten to say exactly this table.

#### The structural moves

`Agent/` trims to `desk/`, `diagnostics/`, `memory/` (plus `Home.md`).
Diagnostics is promoted to first-class: per-family directories (`health/`,
`dreaming/`, `digests/` — as built in 2a; the drafted per-system trio
regrouped to match how the writers actually emit) holding dated
`scorecard-YYYY-MM-DD.md` files with a `latest-scorecard.md` pointer, dated
one-off audits beside them, and a generated `moc-diagnostics.md`. It absorbs
`desk/diagnostics`, `desk/briefs` (a daily digest is a scorecard), and the
human-facing residue of `_meta/health`. Desk becomes work-style: one
directory per in-flight non-project task under `desk/tasks/`, plus a
generated `moc-tasks.md`; `desk/scratch` retires from the vault (ruled) —
existing files sweep to a local non-synced folder, future scratch lives in
session scratchpads and task directories.

This part split at build time (ruled 2026-09-02): everything self-contained
in this repo shipped as **2a**; the Projects merge became the queued **2b**
(`projects-merge`), because moving `desk/projects/` requires the crickets
development-lifecycle plugin's own repoint (resolve_plan, queue-status, the
worker-spawn flow all navigate that path) in a coordinated paired release
this repo cannot ship alone — a cross-repo dependency the sequencing never
named, logged as a plan gap. 2b shipped as v9.11.0, paired with crickets
development-lifecycle v3.37.0 (which shipped first, as the locked order
required): `Projects/` at the vault root is the only projects folder — the
working trees under `desk/projects/<slug>/` moved there wholesale as
`git mv`, basenames preserved and `_harness/` state intact, `desk/labelling`
folded into `Projects/agentm/labelling`, and every path that resolves
harness state was inventoried and repointed. The root space sits above the
memory root, so the storage seam reaches it through a second backend
instance rooted one level up rather than a `..` Locator — offered only under
the Obsidian witness (`.obsidian/` at the parent, none at the memory root),
never on a bare parent directory (the storage-seam design carries that
decision); readers keep `desk/projects/` as a documented
older probe rung — "discovered, never conjured": nothing creates the root
space, and create-when-absent defaults stay inside the memory root until
the space exists. Machine state leaves
the vault for `~/.local/state/agentm/` (ruled): corpus snapshots, learning
caches, skill-discovery cache, `storage-rules-state.json`, and the dreaming
binary's journals. The engine directory is itself a git repository — the
migration initializes it and the runner commits on cadence — so the
history-durability those files had from the vault's repo survives the exit
rather than being traded away for sync hygiene. `_dream/`
and `_crystallize-staging/` retire — dreaming enriches in place, its
in-flight state journaled in the engine directory. `memory/_always-load`
folds into `standards/` (the always-load surface is standards, as at work),
and the always-load set is size-policed with a hard budget so it cannot
become a per-session context tax.

Every area carries its own generated `moc-<area>.md` as an ordinary file;
`memory/mocs/` holds corpus-level maps only, `moc-root.md` on top.

#### Corpus migration

One scripted, phased migration, with three invariants: basenames are
preserved so name-resolved wikilinks survive every move; the link-check gate
is green before and after each phase; and every phase is a dry-run first.
The map, with the operator's rulings applied:

| population | destination |
|---|---|
| inbox expired cohort (1,630) | **purged**, with a manifest of titles + hashes retained (ruled — the one purge in the plan) |
| inbox live (~1,015) | routed to classes via the type→class map, deduped, low-confidence flagged |
| `_archive` (264) | into their classes with `lifecycle: archived` |
| `_opinions` (470) | into `crystallized/`, keeping `kind: opinion-supplement` (ruled); dream-writer and daemon-reader paths repointed |
| legacy kind-dirs (~560: preferences, preference, 2026, fix, idea, insight, workflow, workflow-pattern, feedback) | deprecations-map mechanical migration into classes |
| `external/primos` (38) | classes by nature, provenance-tagged |
| `desk/projects` (~950) | `Projects/<slug>/` wholesale |

Shipped as part 3 (v9.12.0, 2026-09-03), with the map reconciled to the
census the migration itself took: the expired cohort was not the inbox's
1,630 but **2,638** — the same auto-miner retirements sat in `preferences/`,
`preference/`, `fix/` and `_archive` (whose 264 turned out to be all expired,
so "archive into classes as archived" applied to nothing), and 444 of the
470 opinion supplements carried the status too; the operator ruled the purge
`all-expired`, and the engine offers all three scopes so the manifest carries
every figure. Opinion supplements moved into `crystallized/<opinion>/` **as
lanes** — the accumulate loop's shape, kind kept, served file beside the
crystallized memories — not flattened. `external/primos` held 38 records and
no memory: a project tree, moved whole to `Projects/primos/` rather than
routed. Four record kinds the corpus already carried (`report`, `standard`,
`analysis`, `progress-log`) were registered on the operator's ruling so the
membership gate could flip to strict. The "all six classes populated"
criterion does not hold for `episodic`, `entities` and `mocs` — derived
classes nothing routes to — and was read as "every class that has a
population". The Python capture path still writes the pre-v2 vocabulary
into `_inbox/`; a route pass re-files it, and part 4 retires the writer.
| `desk/scratch` (2,324) | out of the vault to a local non-synced folder |
| empty dirs (`_watchlist`, `_skill-watchlist`, `domains`, `_always-load`) | deleted after any contents fold into standards |

Post-migration, `memory/` contains exactly six populated class directories,
episodic gains the session traces and ingested threads it was designed for,
and the class-population counts land on the daily scorecard so an
empty-shell recurrence would be visible within a day.

#### The write path

Capture keeps its current trigger points (the Stop-hook reflection pass and
explicit `memory_capture`), but every candidate now receives a complete
filing decision at write time instead of an inbox write: type, class,
destination path, and the update relationship to the existing corpus —
**add** (novel), **update** (enriches an existing note), **supersede**
(contradicts one; the old note is never deleted, it gains `superseded_by`
and flips lifecycle), or **noop**. Deterministic signals lead: where
extraction can produce a (subject, attribute) key, an object mismatch is an
automatic supersession and a match is a duplicate — embedding similarity is
a secondary signal only, because similarity cannot distinguish a duplicate
from a contradiction (measured AUROC 0.59; a value-flip is a smaller edit
than a paraphrase). Merge decisions bias conservative: file a probable
duplicate flagged rather than auto-merge, since a bad merge destroys
information silently and a duplicate is recoverable clutter.

Every auto-filed note carries `filing_confidence` and `source:`. Low
confidence is the soft inbox: the note is filed at its real destination
immediately and surfaces in a generated needs-review MOC for the nightly
pass and the operator. Capture volume is gated and reported independently
of filing — the predecessor inbox failed by over-capture outrunning
judgment, and removing the pile removes the old visibility, so a writes-per-
day line on the scorecard replaces it as the alarm. External-source content
is trust-tiered at write time and never treated as instructions; write-time
screening is explicitly not relied on to catch fabricated facts, because
the measured state of the art cannot.

**Shipped (v9.13.0, 2026-09-04; PR #540).** The engine, the class filing on
every writer, the needs-review page, the volume gate and the trust tier
landed as designed, with four calls made in the building. The soft inbox is
a *status read*, not a second exclusion: recall keeps serving `unfiled`
notes — the daemon's own captures are that population, 650-odd of them, and
it rank-penalises them rather than hiding them — and excludes only the
staging states the ingest sweep leaves in place; an early draft excluded
`unfiled` and the retrieval gate's canary fired (why not hide them: hiding
the queue would silently shrink the searched corpus against the indexed
one). The cap is grounded, not guessed: 200 sits above the busiest day on
record (110) and well below the last flood; the contract carries it and
zero disables it. The Go capture's `source:` stays a provenance reference
while the contract's `source:` is the transport vocabulary — two meanings of
one field, recorded as an open question rather than resolved by a rename
inside a part that had not planned one (re-audit when the trust tier is read
at retrieval time). And the labeled sample moved the next work upstream:
the engine's operations were judged right wherever the input was sane; the
miner had admitted a pasted handoff as the operator's speech, cut fix
candidates into report fragments, and filed tool-invocation counts as
procedures — four rulings taken (a marker the miner skips, stop the stubs
and purge the 824, a cause-and-remedy requirement, a durability cue for
HIGH), the next plan — shipped as v9.14.0 the same day (PR #546; the 830 stubs purged on the operator's confirmed count; re-mined, the sample yields 9 candidates where it yielded 20).

#### The calendar

`Calendar/YYYY/` holds per-day, per-facet notes named
`YYYY-MM-DD-<facet>.md`, created only when the day has content for that
facet, with four facets (ruled): `meetings`, `correspondence`, `docs`,
`diary`. Facet membership is the selection rule — a meeting happened,
correspondence produced a reply, decision, or deadline, substantive work
landed in an external artifact — with no per-item importance scoring on
top. `diary` is the zero-bar catch-all: operator quick capture lands there,
the agent records what it observed that mattered, and a pattern recurring
three or more times in diary entries is the promotion trigger for a new
standing facet, the same recurrence gate consolidation already uses.

The bare-date file `YYYY-MM-DD.md` is the generated day index: it lists
whichever facet files exist, links the day's episodic session traces, and
carries the system digest — so opening one note shows the whole day without
forcing storage back into one dilution-prone file. Facet files are
append-only while the day is open (new content as new paragraphs, cheap for
the paragraph-aware chunker to re-index); once a day has closed, a
correction is a new dated entry linked with `supersedes`, never a rewrite —
the register's value is the trajectory of what was believed at the time.
Weekly and monthly rollups (`YYYY-Www-review.md`, `YYYY-MM-review.md`) are
generated unconditionally by the dreaming binary, moving the periodic-review
layer from human discipline, which the practitioner record shows failing,
to schedule. The calendar and `episodic/` remain distinct, cross-linked
surfaces: the calendar is the operator's agent-assisted memory of the day;
episodic is the agent's memory of its own sessions.

**Shipped (v9.15.0, 2026-09-04; PR #548).** The four ruled facets write
under the vault-root `Calendar/YYYY/` through the same Obsidian witness the
`Projects/` space uses — the vault root when the memory root is nested,
never conjured (why not create it: the vault root is the operator's, and a
conjured folder on a flat vault would land in the home directory; re-audit
if a flat vault ever needs a register — the writer then needs the operator's
instruction, not a `mkdir`). Facet notes are append-only while the day is
open; a closed day refuses the append and names the correction, which is a
new dated note carrying `supersedes:` and `corrects:` back to an original
that stays byte-identical, listed by both days' indexes. The day index is
generated from what exists and rewritten byte-stably after every append; it
already links the day's episodic traces, none of which exist until part 6
writes them. The rollups ride the Python dream cycle's weekly cadence for
now — every closed week in the last eight, the running month and the one
before, sparse weeks reading sparse and empty weeks saying so once (why not
only the weeks with entries: the review moved from discipline to schedule,
and an honest empty line is the record; re-audit if empty reviews pile up as
noise for a register nobody writes to — then gate on any entry in the
window); the binary takes them over in part 6. The promotion trigger is a
diary label on three or more distinct days in thirty, and its output is a
confirm-gated proposal — the contract file with one line added under
`facets:` — never applied by the agent (why not auto-apply at the recurrence
gate consolidation uses: the registry is the operator's, per part 1;
re-audit only if every proposal is confirmed for a quarter, and then toward
a lighter confirm surface, never toward auto-apply). Two residuals:
`Calendar/` sits outside the daemon's index scope, so recall does not search
the register yet; and the operator's own flat daily notes already live in
`Calendar/`, untouched and unread by the register — whether Obsidian's
daily-notes template and the register become one thing is a call for part 6
or later.

#### Lifecycle and the dreaming binary

One frontmatter axis, `lifecycle`, five values. `pinned` never decays.
`active` is the default. `dormant` is rank-demoted by the daemon's existing
penalty machinery (the `tiers` package reads the field as a continuous
demotion curve). `archived` is hidden from default retrieval — strongly
penalized or excluded from the recall walk, cold-indexed, on disk, in
place. `superseded` points at its successor. Files in the memory classes
never move for lifecycle reasons; directory-move archiving survives only
for low-link-density operational artifacts (plan close-outs keep their
`archive/` convention), and the condition that makes that safe — nothing
links into them — is stated so the boundary stays principled. Who decides
is tiered by reversibility: active↔dormant runs automatic by policy, every
transition logged and summarized in a weekly what-quietly-sank digest;
entering `archived` is operator-confirmed or at minimum conspicuously
reviewable; purge — actual deletion — is operator-initiated only, always,
with a manifest. The demotion-curve parameters are explicitly unmeasured in
the literature for our regime and get tuned by measurement on our own
corpus.

The dreaming binary is the second Go binary beside `agentmd`, porting the
Python layer, and its defining property is **mutation authority**: it may
supersede, merge, re-flag, and re-file what write time got wrong — the
evidence is direct that write-time-only judgment has a structural ceiling a
mutation-time pass recovers. Its job list: the mutation pass over recent
filings and flagged notes; recurrence-gated promotion into `crystallized/`
(provenance links required — a checked invariant, since distillation that
discards sources is measurably worse); entity-file maintenance; MOC
regeneration with a context phrase per link, created at five members, split
past forty, staleness-flagged past ninety days; calendar rollups;
relative-to-absolute date conversion in aging notes; the scheduled
vocabulary audit; class-distribution and volume trend checks; and a sampled
re-classification diff whenever the filing model version changes. It runs
triggered-and-exits — never a resident daemon — dual-gated on elapsed time
and activity, lock-coordinated, journaling to the engine state directory.
Until the port reaches parity (verified by fixtures against recorded
Python-pass outputs, not mirror tests), the Python layer keeps running and
the binary lands job-by-job.

**Shipped (v9.16.0, 2026-09-05; PRs #550–#555, #556).** The axis ranks as
classes at the standard demotion (the daemon's own sweep found every weight
past the standard one a regression, so the "demotion-curve parameters" are
not a knob), `pinned` lands in `durable`, and `archived` sits behind a wall
only an explicit query lifts — recall mirrors it. The governance lanes
journal every move; `archived` only through the confirm surface; purge
operator-only with a manifest. The binary carries the whole job list except
entity-file maintenance, which the write path's entity rollups already own;
relative dates are glossed additively (`last week (the week of 2026-08-24)`),
never rewritten, since the corpus is the operator's words. Parity is a
recording of the Python layer's decisions with the clock pinned, reproduced
by the Go planners; the layers ran side by side through an overlap window
with a daily divergence review, the one review agreed on every surface, and
the operator flipped the takeover the same day — `-apply` on the binary, the
three Python lanes removed, the recording kept as the contract. The overlap
window's length was a gap this design left open; it closed by operator
ruling.

## Alternatives Considered

**Amend `agentm-rescope-filing` instead of a new design.** Rejected (ruled):
the scope — whole vault, write authority, calendar, lifecycle, a new binary
— exceeds that design's filing-contract charter; it gets a Document History
cross-reference here and its surviving decisions are inherited unchanged.

**Keep the staging inbox, fix the routing.** Rejected: the inbox failed on
its own terms in this exact vault (2,652 notes, 61% expired, routing never
built), no surveyed production system stages, and the queue's one useful
property — a place low-confidence decisions can be found — survives as
frontmatter. The bet's known ceiling is priced in via the mutation pass.

**Directory-move archiving.** Rejected for memory classes: it fights the
ID-stability invariant, Obsidian's link rewriting does not cover moves made
outside the app while this vault syncs through Google Drive, and a moved
note silently detaches from every MOC naming it. Retained for plan
close-outs, where link density is near zero.

**Entity subdirectories (the work pattern).** Rejected for home: the two
most authoritative typed-entity systems (Neo4j multi-label nodes, Wikidata
multi-valued instance-of) both reject exclusive placement because real
entities carry two types; home gets the same browsing surface from
generated per-subtype MOCs over a flat `entities/`.

**Replace the cognitive classes with a flat fact store or temporal graph
(Mem0/Zep shape).** Rejected: the classes' measured value is operator
legibility — one of this vault's two audiences — the derived classes carry
the actual retrieval evidence, and a markdown vault is a hard requirement,
not an implementation detail.

**Snake_case with subtype prefixes (the R6 recommendation, the work
habit).** Rejected on R6's own decision rule applied to this corpus:
separators are retrieval-neutral, home's machine-written files are
overwhelmingly kebab already, and renames are evidence-backed non-free. The
retrieval-relevant half — a meaningful subtype word in the filename —
is kept.

**Keep engine state in the vault (`_meta`).** Rejected (ruled): opaque
caches serve neither audience in Obsidian, cost Drive sync, and the field's
disk-not-vault distinction requires only durable disk.

## Dependencies

The storage-rules runtime (daemon `rules` holder plus `storage_rules.py`);
the daemon's `door`, `tiers`, `index`, `capture`, `enrich`, and `sources`
packages as the seams v2 extends; the session hooks in `~/.claude/hooks/`
and both launchd jobs; the crickets development-lifecycle plugin for the
part plans this design sequences into; Obsidian's name-based wikilink
resolution (load-bearing for the migration invariant) and the Google Drive
sync path; and the research bundle at
`Projects/agentm/_harness/research-filing-v2/` (moved with the tree in 2b) as the evidence record —
including its evidence-versus-judgment ledger, which this design treats as
binding on how confidently each choice is stated.

## Migrations

The corpus migration is Detailed Design's third part; what belongs here is
the deployer's view. Order: rules and gates first (so the target state is
checkable), structural moves second, corpus third, each phase dry-run then
applied, link-check green before and after. The purge of the expired cohort
executes only after the manifest is written and the operator has seen the
count one final time. Path repoints (hooks, daemon config, scripts
referencing `_meta`, `_inbox`, `desk/projects`, `_opinions`) ship in the
same change as the move they track, inventoried by grep before any file
moves. Migration runs execute under a quiesced daemon and runner, and that
precondition sets the collision doctrine (learned in the 2a apply): when a
move finds its destination already occupied, the vault copy wins — it is by
definition the last production write, whatever sits at the destination
predates the migration, and the engine repository's history preserves what
gets replaced. A bulk move also re-keys every moved note for the dense arm,
and the daemon re-walks the tree without backfilling those vectors, so a
move's run order ends with `agentmd embed` (learned in the 2b apply: the
retrieval gate read 0.734 → 0.438 until the backfill restored it exactly).
Two more from the part-3 apply: a later pass over a corpus the daemon kept
writing into settles a routed note against a file already home (identical
body → filed superseded as `~dup`, different body → a basename clash) rather
than refusing, so the migration stays resumable; and an instrument that pins
paths — the retrieval gate's gold set — compares them in a canonical
basename form once the corpus has moved, or its control fires on the move
itself. The purge is operator-confirmed by exact count (`--confirm-count`),
never defaulted.
Rollback for every phase except the purge is `git revert` in the
vault repository plus replaying the path repoints; the purge is
non-rollbackable by design and bounded by its manifest. The Python dreaming
layer is not retired until the Go binary's parity fixtures pass; the two
run side-by-side with the binary in report-only mode during the overlap.

## Technical Debt & Risks

- **Over-capture can recur invisibly.** The inbox pile was the old alarm;
  dispersal into six directories hides it. Mitigation: the volume gate and
  the writes-per-day scorecard line. Re-audit trigger: writes-per-day
  doubling week-over-week, or class counts growing faster than sessions.
- **Write-time filing has a structural error ceiling.** Priced in via the
  mutation pass, confidence flags, and conservative merges — but the
  mutation pass is load-bearing: shipping the hook without it is the one
  configuration the evidence argues against. Re-audit trigger: needs-review
  MOC exceeding ~5% of weekly filings.
- **Demotion-curve parameters are unmeasured.** The graduated-status shape
  is convergent practice, not a measured result (one citation offered as
  direct evidence was found on verification to address a different
  problem). Tune on our own corpus; re-audit trigger: stale content
  surfacing above better answers in recall audits.
- **Capture-time poisoning is unsolved industry-wide.** Fact-shaped false
  content passes write-time screening at measured rates approaching total.
  Mitigation is exposure reduction (provenance trust tiers), not detection.
  Re-audit trigger: email ingest landing (it widens the untrusted surface).
- **The Go port can drift from the Python behavior.** Parity fixtures must
  assert recorded outputs, not recompute expectations with the new code
  (the mirror-test trap). Re-audit trigger: any parity fixture rewritten
  during the port.
- **Session-grant enforcement is new surface.** A validator gap would let
  writes into `Projects/` without a grant. Mitigation: deny-by-default in
  the authority table; grant state carried in session context, not config.
- **The empty-shell failure can repeat.** Rescope-filing shipped structure
  and the corpus never followed. v2 ships structure and population in the
  same arc, and class-population counts sit on the daily scorecard.
  Re-audit trigger: any class at zero thirty days after part 3 completes.

## Quality Attributes

### Security

The write-authority table is the boundary model: deny-by-default for the
operator's spaces, session-scoped grants for `Projects/`, purge as an
operator-only act. Capture-time prompt injection and memory poisoning are
handled by provenance trust tiers and by never treating memorized content
as instructions — explicitly not by trusting the filing model to detect
fabricated facts, which the measured evidence says it cannot. Engine state
moving to `~/.local/state/agentm/` takes cursors and caches out of the
synced, shared surface.

### Reliability

The rules block stays fail-closed: unparseable rules halt filing loudly.
The dreaming binary is triggered-and-exits with journaled, lock-guarded
state in the engine directory, so a crash mid-pass resumes instead of
losing work — the named failure mode of in-process background queues. The
Python layer retires only after parity, with a report-only overlap.

### Data Integrity

ID-stability is preserved by construction: lifecycle is frontmatter, memory
files never move after migration, and the migration itself preserves
basenames so name-resolved wikilinks survive. Nothing is deleted outside
the operator-gated purge, which is manifested. Supersession retains the old
note with a forward link. Crystallized provenance is a checked gate. The
link-check gate brackets every migration phase.

### Privacy

`Personal/` is exempt from the memory contract and off-limits without
per-task instruction. The calendar respects the existing no-pii gate, and
sensitive material routes to memory rather than the browsable daily
register. The purge path plus lifecycle give true deletion a defined,
operator-only lane. Provenance fields record transport, not content, and
stay inside the vault.

### Latency

Filing adds LLM judgment at capture points (session stop, explicit
capture), not per turn; recall paths are untouched. Append-only calendar
files keep incremental re-indexing cheap under the paragraph-aware chunker.
Retiring scratch and the expired cohort shrinks the dense index by roughly
half its note count, which can only help the retrieval budget.

### Scalability

The vault sits at ~8,000 notes today — inside the range where Obsidian's
graph is decorative and unscoped queries slow, but below file-explorer
failure. v2 reduces indexed volume (scratch out, purge, engine state out),
keeps hierarchies shallow, scopes any query surface it adds, and treats the
graph as a diagnostic, not navigation. Facet-per-file keeps embedded units
topically homogeneous as the calendar grows.

### Testability

Every new rule lands as a deterministic gate (vocabulary membership and
collisions, provenance presence, lifecycle enum, link check, authority
validator), extending `check-all.sh`. The port is verified by recorded-
output parity fixtures. Quantitative claims — demotion thresholds, filing
accuracy, facet retrieval value — are measured on this corpus with local
judges; published benchmark numbers were found unusable for design choices
and are not cited as verification anywhere in this design.

## Project management

### Work estimates

| part | scope | est. |
|---|---|---|
| rules, vocabulary, and write authority | storage-rules v2 edit, validator, gates | M |
| structural moves | directory moves, engine-state exit, standards absorption | S |
| corpus migration | routing engine, purge, legacy dissolution | M |
| the write path | filing decision, update ops, confidence, volume gate | M |
| the calendar | facets, day index, rollups | M |
| lifecycle + dreaming binary | status curve, mutation pass, Go port | L |

### Documentation Plan

Vault-side: `Filing.md` and `index.md` rewritten; `standards/storage-rules.md`
prose sections updated alongside the block. Repo wiki: this design;
`reference/CI-Gates.md` (new gates); `reference/Memory-Daemon.md` (lifecycle
ranking, filing endpoint, the second binary); a how-to for the vault layout
and write-authority model; `Designs.md` / `_Sidebar.md` entries. The
research bundle stays where it is as the evidence record.

### Launch Plans

Phased by part order; parts 1–3 land before 4–6 begin (the vault must be
true to the rules before the rules go live at write time). No dates set;
each part ships through the normal `/work` → `/review` → `/release` cycle
with wake-on-CI close-out.

## Operations

### Monitoring and Alerting

Daily scorecards in `Agent/diagnostics/` carry the new lines: writes per
day, class populations, needs-review count, lifecycle transition counts.
The weekly what-quietly-sank digest surfaces automatic demotions for
review. The health-score battery gains the new gates. The operator's alarm
surface is the scorecard plus the digest — no push alerting is added.

### Logging Plan

The dreaming binary journals every mutation (what changed, why, which rule)
append-only in the engine state directory; lifecycle transitions log with
their trigger; the purge writes its manifest before deleting. Journals are
plain files, greppable, retained indefinitely (they are small).

### Rollback Strategy

Rules edits revert via git (the vault is a repository; the rules hash makes
staleness visible). Structural and corpus moves revert via git plus
replaying path repoints — preserved basenames make reverts link-safe. The
purge alone is irreversible, which is why it is manifested and
operator-gated. The dreaming binary rolls back by re-enabling the Python
layer, which is not retired until parity fixtures pass.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-09-01 | Initial draft created via `/design author`, drafted from the decided filing-v2 synthesis (six research lanes, vault census, and operator rulings — see `desk/projects/agentm/_harness/research-filing-v2/`); review pass ran the same session — all sections and quality attributes approved unrevised — and the operator approved as final. Translated to 6 parts via `/design translate` (operator-approved split): rules-and-authority, structural-moves, corpus-migration, write-path, calendar, lifecycle-dreaming — part files at the vault's `_harness/designs/agentm-filing-v2/parts/`. Sequenced into 6 draft plans via `/design sequence`: `PLAN-rules-and-authority.md` active (named-plan mode, beside the unrelated online-recall plan), five queued at `_harness/designs/agentm-filing-v2/queued-plans/`. | final |
| 2026-09-02 | Part 2 reconciled to what shipped (v9.10.0, PRs #521/#522). Three amendments: (1) structural-moves **split 2a/2b** — the Projects merge needs the crickets development-lifecycle repoint in a paired release this repo cannot ship alone (why not ship it here: a one-repo move would strand every plan-resolution path; re-audit when 2b lands — fold this paragraph back to one part). (2) The engine state dir is a **git repository** (migration inits, runner commits on cadence) — why not plain files: the exit from the vault would silently trade away the history-durability those files had from the vault's repo; re-audit if the runner's commit cadence ever stalls (a dirty engine repo older than a week is the tell). (3) Diagnostics directories are **per-family** (`health/dreaming/digests`), not the drafted per-system trio — why: the writers emit by family, and the drafted names would have forced a router nothing needed; plus the Migrations section gains the **vault-wins collision doctrine** learned in the live apply (quiesce makes the vault copy the last production write; skip-on-collision stranded real state behind scratch leakage). | final |
| 2026-09-03 | Part 2b (projects-merge) reconciled to what shipped (v9.11.0, PRs #528/#529; crickets development-lifecycle v3.37.0 first, the paired order as locked). Body: the structural-moves paragraph folds 2a/2b back to one shipped part. Two decisions recorded: (1) the vault-root `Projects/` space is reached through a **second seam backend instance** rooted at the vault root — why not a `..` Locator: Locators are root-confined by construction, and a relative escape would silently mis-root every later join onto the memory root (the storage-seam design carries the mechanism and its re-audit triggers) — the pre-tag review added the **Obsidian witness**: the sibling counts only when the memory root is nested inside a vault (`.obsidian/` at the parent, none at the memory root), because a flat vault's parent is the operator's home, where a `Projects/` is common, and the flat generation `<memory-root>/Projects` now reaches every walker and writer; (2) readers keep `desk/projects/` as a documented older probe rung — why not drop it: "discovered, never conjured" needs the rung for a vault that has not moved, and it costs one `is_dir`; re-audit when part 3 retires the last desk-era path, then drop the rung. Migrations gains the **embed backfill invariant** (a move re-keys the dense arm; `agentmd embed` closes the run) — learned in the 2b apply. Dependencies now names the moved research bundle. | final |
| 2026-09-03 | Part 3 (corpus-migration) reconciled to what shipped (v9.12.0, PRs #533–#537; applied live the same day). Body: DD§3's map gains the shipped paragraph — the expired cohort was 2,638 across populations, not the inbox's 1,630 (why not purge the inbox alone: the same auto-miner retirements sat in the legacy dirs, `_archive` and 444 supplements, and leaving them as `lifecycle: archived` would have kept a rank-penalized junk corpus on disk against the design's own "maps it away"; re-audit if a future miner produces an expired cohort worth keeping); supplements kept their lane shape under `crystallized/<opinion>/` (why not flatten: the accumulate loop's nine consumers walk lanes, and one resolver moved them all); `external/primos` moved to `Projects/primos/` as a project tree (why not route: it held records, no memory); four record kinds registered (why not re-kind: 45 live records already meant those shapes). Migrations gains the resumable-pass and canonical-instrument invariants and the confirmed-count purge. Residual named for part 4: the Python capture path's pre-v2 vocabulary. | final |
| 2026-09-04 | Part 4 (write-path) reconciled to what shipped (v9.13.0, PR #540; deployed live the same day). Body: DD§4 gains the shipped paragraph — the soft inbox is a status read that keeps `unfiled` served (why not exclude it: the daemon's own captures are that population and the canary fired when a draft did; re-audit if the enrichment queue ever drains to zero and `unfiled` stops meaning "the daemon's backlog"); the cap grounded in the corpus (200 over a busiest day of 110); the two meanings of `source:` recorded as an open question (why not rename in this part: no writer-side plan carried it; re-audit when the trust tier is read at retrieval time); and the labeled sample's finding that the next work is the miner's, with the operator's four rulings. | final |
| 2026-09-04 | The write path's follow-up (miner-provenance, v9.14.0, PR #546) reconciled: DD§4's shipped paragraph records the four rulings as shipped. No locked call changed; the miner is upstream of this design's write path, and its rules live in reflect.py's pattern tables (why not fold the miner into the design: it predates filing v2 and serves every part; re-audit when a later labeled sample disowns a "User stated" note again). | final |
| 2026-09-04 | Part 5 (calendar) reconciled to what shipped (v9.15.0, PR #548; live the same day). Body: DD§5's calendar section gains the shipped paragraph — the register discovered through the Projects witness and never conjured (why not create it: the vault root is the operator's; re-audit if a flat vault needs a register); rollups on the Python dream cadence until the binary takes them over in part 6, empty weeks saying so once (why not skip them: schedule replaced discipline; re-audit if empty reviews become noise); promotion as a confirm-gated rules proposal, never auto-applied (why not auto-apply: the registry is the operator's per part 1; re-audit only toward a lighter confirm). Residuals named: the register outside the daemon's index scope; the operator's flat daily notes coexisting in `Calendar/`. The write path gains one amendment this part's CI found at UTC midnight: the volume gate counts against the day the arriving note's `captured` stamp names, the same day the writes-per-day reading files it under (why not the wall clock: the gate and the reading disagreed about which day a write belonged to, and a flood in progress at midnight found the door open; re-audit if a writer ever backdates `captured` deliberately — the gate would count against the backdated day). | final |
| 2026-09-05 | Part 6 (lifecycle-dreaming) reconciled to what shipped (v9.16.0, PRs #550–#555, #556; live the same days) and the design launched. Body: DD§6 gains the shipped paragraph — the axis as classes at the standard demotion (why not a tuned curve: the daemon's 125-point sweep found every other weight a regression; re-audit when the corpus has age spread); additive date glosses (why not rewrite: the corpus is the operator's words and every other mutation is additive; re-audit if glossed notes read badly in recall excerpts); the takeover as one change to two manifests after a written disposition of the divergence reviews (why not a longer window: the one review agreed on every surface and the operator ruled; re-audit if a first applying pass ever skips an intent). Gap closed by ruling: the overlap window's length and who closes it. | launched |
