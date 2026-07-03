---
title: memory index — design
status: launched
kind: design
scope: feature
area: agentm/memory-index
governs: []
parent: agentm-hld.md
seeded: 2026-06-26
approved: 2026-06-26
---

> **The memory index (V6-11) is the SQLite metadata table that lets recall filter by `kind`, `project`, `tag`, and `status` in one query alongside the vector search — replacing the grep-over-frontmatter pass.** It lifts the queued V6 plan into a tracked design, reconciled to the built index and extended with the fingerprint column + two new memory kinds; parent [agentm HLD](agentm-hld), sibling to [memory system](agentm-memory-system).

# AgentM Memory Index Design

## Objective

Recall today runs a vector search plus a grep over each entry's frontmatter — there is no structured way to ask for "the `security` entries in `project: sherwood`, updated this month." V6-11 adds that: a SQLite **metadata table** beside the existing vector index, joined by row id, so a recall is one SQL `WHERE` over `kind` / `project` / `tag` / `status` AND a vector `MATCH` in the same query. It is the substrate the [diagnostics](https://github.com/alexherrero/crickets/wiki/crickets-diagnostics) recall ladder and [token-audit](https://github.com/alexherrero/crickets/wiki/crickets-token-audit)'s session-cost capture both wait on.

The contract for this lived only in the queued V6 plan in the vault, which no tracked design referenced — so four dependents read as buildable while resting on an unwritten substrate. This design lifts that plan into the governed tree, reconciles it to what the index actually is today (a four-column table awaiting additive columns), and folds in the three extensions the AG track added: the **fingerprint** column, and the **`session-cost`** and **`failure-incident`** memory kinds.

## Overview

Markdown stays the source of truth. The index is a derived, device-local cache the recall loop reads; this slice widens its metadata so a query can filter and rank in one pass.

![Markdown entries are the source of truth; the vec_index drain/full-sync walk hydrates two device-local SQLite tables joined by row id — entry_meta (the metadata: path, kind, status, tags, project, fingerprint, …) and entries (the FLOAT[1024] embeddings); hybrid recall runs one SQL WHERE over the metadata plus a vector MATCH, with the grep-over-frontmatter pass kept as the graceful fallback when sqlite-vec is absent; the fingerprint column feeds the diagnostics recall ladder](diagrams/agentm-memory-index.svg)

*Markdown is the source of truth; the drain walk hydrates the two row-id-joined SQLite tables; hybrid recall is one SQL `WHERE` + a vector `MATCH`. Grep stays the graceful fallback; the fingerprint column feeds diagnostics.*

## Design

### What is built today

The index is real and device-local (the [memory system](agentm-memory-system)'s local-index tier, never synced):

- **`vec_index.py`** — a `entries` vec0 virtual table (`FLOAT[1024]`) plus a metadata table **`entry_meta(rowid, path, updated_at, indexed_at)` — four columns**, joined to the vectors by `rowid`. It has a JSONL embedding queue, an async drain, drift detection (the source of `indexed_at`), and a dimension-mismatch `rebuild_index`.
- **`recall.py`** — the recall loop: tokenize → vector search → drift-check-and-drop → grep over slug, tags, title, and the first 500 chars → merge (`sim × 0.85 + keyword × 0.05`) → top-5. It graceful-degrades to grep-only when sqlite-vec or embeddings are absent.

V6-11 extends this; it does not replace it.

### The extended metadata table

Add columns to the built `entry_meta` by **additive, idempotent migration** (an `ALTER TABLE ADD COLUMN` guarded by a column check, mirroring the existing `_migrate_pre_v37` step):

| Column | Source |
|---|---|
| `kind` · `status` · `slug` · `project` · `created` | the entry's frontmatter |
| `tags` (JSON array) | frontmatter |
| `group_name` | frontmatter `group` (renamed — `group` is a SQL keyword) |
| `fingerprint` | the AG-added join key for the diagnostics recall ladder |

Row id stays the join to `entries`. The `rebuild_index` contract extends to repopulate these columns from frontmatter on a `full-sync`, alongside the embeddings; its `CREATE TABLE entry_meta` must carry the new columns too, or a rebuild would drop them. The design declares which columns are indexed (`kind`, `project`, `status` carry a `CREATE INDEX` for the filter path). The built table starts at four columns, so the migration is purely additive.

### Building the index from source

The index is device-local and never synced, so a fresh machine has the synced markdown but no index — it builds one from the vault. The markdown is the source of truth; the index is a derived cache rebuilt locally from it, the same way on a new device, after a wipe, or after a corruption.

The cold build reuses the two built operations. `full_sync --rebuild` walks the vault (`private/` · `projects/` · `incubator/`) and enqueues every entry; `drain` then embeds each one and populates both tables — the `entries` vectors and the `entry_meta` row, with the V6-11 metadata columns read from each entry's frontmatter in the same pass. The build is resumable: the enqueue is a plain JSONL append and the drain is cursor-backed, so an interrupted first sync continues where it left off and a re-run is idempotent.

The two halves split by dependency: enqueueing needs no `sqlite-vec` or embedding model (it is an append), so the walk runs anywhere; only the drain needs the local embedding model. Embedding every entry is a one-time per-device cost, and recall graceful-degrades to grep-only until the build finishes — so a fresh agent keeps recalling (without the vector half) while the drain catches up. The fresh index is created with the extended schema, so a cold build produces the full metadata table directly. A dedicated `reindex` convenience subcommand is anticipated; `full_sync --rebuild` is that path today.

### Hybrid recall

Add a `--filter` path to `recall.py query`: an expression like `tag=security AND project=sherwood` compiles to one SQL `WHERE` over the metadata, joined with the vector `MATCH`, in a single query. This **replaces the grep-over-frontmatter pass** for the filtered case, while grep stays the graceful fallback when sqlite-vec is absent. The merge weighting is unchanged (`sim × 0.85 + keyword × 0.05` — the live constants; the `0.7 / 0.3` in the comments is stale). Operator surfaces follow: `/memory search --tag --project`, `/memory list --kind --updated-since`. Out of scope (from the source plan): materializing entry bodies into SQLite, and LLM-extracted metadata.

### The two new kinds + the fingerprint

`kind` is already a free-form kebab-case taxonomy (a path segment + a display header), so the two new kinds are **reserved values of the existing `kind` taxonomy**:

- **`session-cost`** — a recall-eligible kind that [token-audit](https://github.com/alexherrero/crickets/wiki/crickets-token-audit)'s session-cost capture writes and the dreaming session-cost review reads.
- **`failure-incident`** — the kind the [diagnostics](https://github.com/alexherrero/crickets/wiki/crickets-diagnostics) recall ladder writes. Its write carries a **mandatory privacy scrub**, because failure context is untrusted and PII-bearing — a persistence-boundary guard the write cannot skip.

The **`fingerprint`** is a real column — a join/lookup key the diagnostics ladder matches on.

### The DerivedMaintenance relationship

The [memory system](agentm-memory-system) reserves a `DerivedMaintenance` extension point for exactly this kind of derived index (it points the reader at the storage seam, though the seam text does not yet carry the reservation), and nothing implements it yet — the built `vec_index.py` is a parallel working implementation. This slice **builds on `vec_index.py`** (its drain, drift detection, and rebuild already work) and names `DerivedMaintenance` as the contract it eventually satisfies, with a re-audit trigger so the reconciliation stays on record.

### The boundary

- **vs [memory system](agentm-memory-system)** — the memory system owns the recall loop, the capture path, the tiers, the local-index tier, and the `DerivedMaintenance` extension point; this design is the indexed-recall slice it marks designed-for (the V6 milestone), specifying the metadata table + the hybrid query, and a future `DerivedMaintenance` implementer.
- **vs [storage seam](memory-storage-seam)** — the seam owns the read/write verbs, the `Locator` guards, and the T1/T2/T3 ownership tiers; this index is a derived device-local cache the recall path reads, beneath that contract.
- **vs the rest of V6** — V6-11 is the metadata table only. The hybrid-rank engine (V6-3, RRF over BM25 + vector), the typed-entity graph (V6-2), and chunking (V6-10) stay deferred; this slice is pulled forward on its own because its dependents need structured filtering; ranking stays deferred.

## Dependencies

- **extends the built index** — `vec_index.py` (the table + drain + rebuild) and `recall.py` (the query path).
- **rides the [memory system](agentm-memory-system)'s local-index tier** (device-local, never synced); a future `DerivedMaintenance` implementer (the extension point memory-system reserves).
- **feeds [diagnostics](https://github.com/alexherrero/crickets/wiki/crickets-diagnostics)** (the `failure-incident` kind + the `fingerprint` recall ladder) and **[token-audit](https://github.com/alexherrero/crickets/wiki/crickets-token-audit)** (the `session-cost` kind).
- **composes [privacy](https://github.com/alexherrero/crickets/wiki/crickets-privacy)** — the mandatory scrub on a `failure-incident` write.
- Points up at the [agentm HLD](agentm-hld) §Memory; the recall loop + tiers are the [memory system](agentm-memory-system).

## Migrations

- **This is a lift.** The canonical V6-11 contract lived only in the queued V6 plan in the vault (`_harness/ROADMAP-AgentMemoryV6.md`). This design migrates it into the governed tree, reconciled to current truth. **Re-read the live source at fold** in case it drifted since it was queued — the schema here is reconciled from the queued spec plus the AG-added axes.
- **Additive migration only.** The built `entry_meta` is four columns; the migration is `ALTER TABLE ADD COLUMN` (idempotent, guarded), back-filled on the next `full-sync`. A clean-slate `CREATE TABLE` would mis-describe current truth and risk a drop-and-recreate.
- **At lift (docs):** add the `agentm/memory-index` area to the area-taxonomy; have [memory system](agentm-memory-system) point to this slice as the V6-11 specification; downgrade the dependents' status language from "designed" to "blocked on V6-11" where they don't already say so (the runner already does).

## Risks & open questions

- **Buildability illusion.** Until this lands, the diagnostics first-slice deterministic layer, token-audit session-cost capture, the runner health-check report, and the dreaming session-cost review read as independently buildable while resting on an unspecified substrate. The lift closes the spec gap and re-points those dependents.
- **The parallel-implementation tension.** `vec_index.py` sidesteps the seam's reserved `DerivedMaintenance`. Building on `vec_index.py` is the pragmatic call, but the seam's never-implemented contract rots silently if this is left unsaid — hence the named re-audit trigger.
- **`failure-incident` is a leak surface.** Its write carries untrusted, PII-bearing context; the mandatory privacy scrub is a persistence-boundary guard the write cannot skip.
- **Source-of-truth drift.** The lifted spec was queued 2026-05-27; re-read the live vault plan before folding in case it changed.
- **Re-audit triggers:** re-read the live V6 plan at fold; reconcile onto `DerivedMaintenance` if/when the seam path is implemented; flip `[PENDING-IMPL]` as the table + hybrid query land; revisit when V6-3 (RRF) arrives to consume the table.

## Locked design calls

- **A standalone Wave-B slice.** Ship the metadata table + SQL filtering + the fingerprint on their own to unblock diagnostics and token-audit; the source plan's pairing with V6-3 (RRF) is deferred — V6-3 consumes this table later.
- **Additive migration only.** Extend the built four-column `entry_meta` by guarded `ALTER TABLE ADD COLUMN`, mirroring `_migrate_pre_v37`; the rebuild path's `CREATE TABLE` carries the new columns too.
- **`group_name` for the frontmatter `group` key.** The bare `group` is a SQL keyword.
- **The two new kinds are reserved `kind` values.** `session-cost` and `failure-incident` ride the existing free-form `kind`; the `fingerprint` is the one real new column.
- **Build on `vec_index.py` now; `DerivedMaintenance` is the eventual contract** (re-audit trigger recorded).
- **Markdown stays the source of truth.** The index is a derived, device-local cache; nothing here moves authority into SQLite.

## References

- **Lifted from:** the queued V6 plan (`_harness/ROADMAP-AgentMemoryV6.md` — the V6-11 metadata-table spec)
- **Extends (built):** `harness/skills/memory/scripts/vec_index.py` (the four-column `entry_meta` + `entries` + drain + rebuild) · `recall.py` (the five-step loop + the `sim × 0.85 + keyword × 0.05` merge)
- **Composes:** [memory system](agentm-memory-system) (the recall loop + tiers + the local-index tier + the `DerivedMaintenance` reservation) · [storage seam](memory-storage-seam) (the read/write verbs + ownership tiers) · [privacy](https://github.com/alexherrero/crickets/wiki/crickets-privacy) (the failure-incident scrub)
- **Consumers:** [diagnostics](https://github.com/alexherrero/crickets/wiki/crickets-diagnostics) (the `failure-incident` + `fingerprint` recall ladder) · [token-audit](https://github.com/alexherrero/crickets/wiki/crickets-token-audit) (the `session-cost` kind) · the [runner](agentm-runner) health-check report
- **Up:** [agentm HLD](agentm-hld) §Memory · [memory system](agentm-memory-system) (the V6 indexed-recall trajectory)

## Amendment log

*Newest first. Collapses to one ≤2-paragraph entry at finalization; git holds the granular history.*

- **2026-07-03 — `drain` fails loud on a missing vault instead of returning stale-looking empty stats.** `vec_index.py`'s `drain` subcommand now checks `vault.is_dir()` before calling `drain_queue()` and exits 1 with an error if the vault directory doesn't exist, rather than falling through to `drain_queue()`'s default `{"processed": 0, "skipped": 0, "errors": 0, "remaining": 0}` — a snapshot indistinguishable from a legitimately empty queue. This is a deliberate narrow exception to the module's otherwise-consistent graceful-degradation philosophy: read-only walkers like `find_drifted_entries` correctly stay graceful on a missing vault, but `drain` is a mutating operation where "vault vanished mid-run" must be distinguishable from "nothing pending." Found while writing `scripts/verify-vec-index.sh`'s fault-injection check (R1.4 / agentmExperience#0). *Re-audit trigger:* if another mutating `vec_index.py` subcommand is added, it should get the same existence check.

- **2026-06-28 — lock-down sweep (operator review).** All standing fixes clean (diagram sized; no mermaid; no ADR mentions; log already newest-first). Confirmed markdown stays the source of truth, the three AG-added axes (the `fingerprint` column + the `session-cost` / `failure-incident` reserved kinds, the latter with the mandatory privacy scrub on write), and the hybrid `--filter` recall path. No content change. Locked as a v5–v8 guidepost.

- **2026-06-26 — lifted the V6-11 metadata-table plan into the governed tree; approved + launched.** The last Bucket-A substrate piece, and a *lift*: the canonical V6-11 contract lived only in the queued V6 plan in the vault, which no tracked design referenced, so its dependents rested on an unwritten substrate. This design migrates it, reconciled to the built index (the four-column `entry_meta` in `vec_index.py`, extended additively), and folds in the three AG-added axes: the `fingerprint` column, and the `session-cost` + `failure-incident` reserved `kind` values. It specifies the extended metadata table, the hybrid `--filter` recall path (one SQL `WHERE` + a vector `MATCH`, replacing the grep-over-frontmatter pass, grep kept as the graceful fallback), the build-from-source path (a fresh machine bootstraps the device-local index from the synced markdown via `full_sync --rebuild` → `drain`, resumable, recall grep-degrades until built), and the mandatory privacy scrub on a `failure-incident` write. **Locked:** a standalone Wave-B slice (V6-3 RRF deferred); additive idempotent migration; `group_name` over the SQL keyword; the two kinds are reserved `kind` values; build on `vec_index.py` with `DerivedMaintenance` named as the eventual contract; markdown stays the source of truth. *Re-audit:* re-read the live V6 plan at fold; reconcile onto `DerivedMaintenance` when the seam path is implemented; flip `[PENDING-IMPL]` as the table + hybrid query land.
