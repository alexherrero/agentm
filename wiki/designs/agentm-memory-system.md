---
title: memory-system — design
status: launched
kind: design
scope: feature
area: agentm/memory
governs: [harness/skills/memory/scripts/]
parent: agentm-hld.md
children: [memoryvault.md]
seeded: 2026-06-20
approved: 2026-06-21
---

> [!NOTE]
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21) · locked 2026-06-28 (final AG design sweep).** child-design — the Memory pillar, parent [agentm HLD](agentm-hld.md); inherits the [Foundations HLD](agentm-foundations-hld.md) by reference. The largest pillar — the ground the other three stand on. `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3); the seam content has migrated to the launched `memory-storage-seam` design.

# AgentM Memory System

## Objective

Memory is the **durable record agentm keeps** — what it has learned, the plans and designs it works on, the standards it holds — and the single disciplined path every caller takes to reach it. It is the largest of the four pillars and the ground the other three stand on: **Experience** grows it, **Opinions** keep their learned half in it, **Personas** draw their state from it.

It is built to **compound**: entries are typed, densely linked to their neighbors, and indexed for recall — so as agentm learns and reaches outward, the record grows into an interconnected knowledge base.

## Overview

The substrate serves one aim: **every caller reaches storage the same way, and nothing leaks a dependency downward.** A small set of generic pieces — none knowing a specific backend, host, or tool — arranged so everything points *inward*: a caller reaches the memory engine, the engine reaches storage through the resolution plane and the seam, and the concrete backends, personas, and crickets tools all depend *up*. The substrate depends on nothing below it.

![Whole-component view: in-process callers, MCP clients (opt-in), crickets tools, and personas all depend on the memory engine; the engine reaches the resolution plane then the storage seam; the device-local and obsidian-vault backends implement the seam — every arrow points inward to the substrate](diagrams/agentm-memory-whole-component.svg)

The components:

- **The memory engine** — the verbs the rest of the system calls (`save` · `recall` · `forget`, plus reflection) and the cross-cutting logic that must live exactly once: idempotency + content-hash CAS, soft-delete, token-budgeted recall, link integrity.
- **The resolution plane** — how a call finds its store without naming one: the config holds the backend choice; the selector (`backend_selection.py`) maps a protocol name to a concrete `StorageBackend`, failing loud if it's missing.
- **The storage seam** — the one port to disk: a `StorageBackend` contract, a registry, an opaque `Locator`, and the storage tiers. *(The seam's full contract — the verbs, the `Locator` guards, the tiers + the never-sync invariant, the reserved `DerivedMaintenance` — is the launched [memory-storage-seam design](memory-storage-seam.md); this pillar points down to it.)*
- **Harness-state I/O** — plan/progress/feature state is backend-aware: it routes through the seam to the active backend, so state and memory reach disk the same way.

**The one-way rule, enforced.** Routing and memory code may import the seam + selector (substrate) but **never a concrete backend** — the LC-8 gate (`check-process-seam-import-direction.sh`) fails the build on any `import storage_vault`. The backend is chosen at call time and injected through the abstract contract. Backends and tools point up; the substrate points at nothing below.

## Design

### How memory is classified — three axes

agentm classifies every memory on **three independent axes**: by **kind** (what sort of thing it is), by **durability** (does it survive the session?), and by **ownership** (whose space is it?). Kind drives *retrieval*; durability and ownership drive *placement*.

**By kind.** Every entry declares a **`kind`** — open-ended but conventional: `preference` · `workflow` · `fix` · `domain-reference` · `idea` · `skill-pointer`. Kind is load-bearing: it picks the storage group an entry is written to, and it scopes which entries a phase pulls into its recall budget (a `/work` pass recalls different kinds than a `/plan` pass). Kind is how a caller asks for *the right sort of memory* rather than a flat search over everything.

**By durability + ownership.** Two planes:

- **Layer 0 — agent-harness memory (not durable):** the host's context window — the running conversation. Short-lived; it fills and gets summarized away. Scratch space; the record lives below, on disk. *(The layer the whole substrate exists to compensate for.)*
- **Layer 1 — the durable record (on disk):** resolved at runtime, never a cached path. It splits into **three ownership tiers, by ascending agent autonomy** — and each tier is a place in the vault:

| Tier | What it holds | Where | The agent's hand |
|---|---|---|---|
| **T1 — personal** | the operator's own notes | the Obsidian vault *above* `Agent/` (its siblings) | reads + links; **writes only when told**, through a separate seam call |
| **T2 — curated / collaborative** | designs · plans · roadmaps, and the operator-directives the agent follows (voice, conventions, preferences) | the `Agent/` root — *our* shared drive | writes as needed and **reports each change in the digest**; revertable |
| **T3 — agentm-sole** | the agent's own learned memory + insights | `Agent/Memory/` | writes, curates, and prunes freely; no notice |

Personal (T1) sits *outside* agentm's `vault_path` (which is `Agent/`), so it is out of reach by default, not by policy alone. The agent writes there only through an **explicit, separate storage-seam call** that an operator request authorizes — keeping agent-controlled and user-controlled space cleanly apart (the [memory-storage-seam design](memory-storage-seam.md)). Autonomous jobs work mostly in T3; when a job changes T2, that change lands in the digest for the operator to see and revert.

![The three ownership tiers as a vault hierarchy: the operator's personal vault at the top (T1, untouched), the Agent/ shared drive inside it (T2, agent writes and reports), and Agent/Memory/ within that (T3, the agent's own memory); kind is the orthogonal third axis carried in each entry's frontmatter](diagrams/agentm-memory-layers.svg)

*The diagram shows the nested ownership tiers (containment = autonomy) below the durability line; **kind is the orthogonal third axis**, carried in each entry's frontmatter.*

### The vault layout

The structure is opinionated — the agent controls most of it, so the design fixes the shape:

```
.../Obsidian/                    T1 personal — the operator's vault; agentm does not touch it
├── Church/ · Home/ · Tech/ · …    the operator's own notes
└── Agent/                       T2 curated — agentm's vault_path; the shared drive
    ├── projects/<project>/        designs · plans (_harness) · roadmap · progress
    ├── _always-load/              operator-directives the agent follows (voice, conventions, non-negotiables)
    ├── preferences/ · feedback/   more operator-directives
    ├── _archive/                  retired curated content (cold; recall skips it)
    └── Memory/                  T3 agentm-sole — the agent's own memory
        ├── _always-load/          the heat-promoted floor (flat — kind is not a subfolder here)
        ├── <kind>/<slug>.md       the leaf: kind is the subfolder (insight/, domain-reference/, crystallized/, …)
        ├── _inbox/                raw reflection candidates (recall-excluded by default)
        ├── _idea-incubator/       agent-incubated ideas
        ├── _index.md              a generated map of contents, rebuilt from frontmatter (never hand-kept)
        ├── _archive/              the cold zone (recall skips it)
        └── _meta/                 device-local sidecars — vector index, heat, embedding queue; derived, never synced as truth
```

**T2 (curated) shape.** Curated content lives under `projects/<project>/` — each project carrying `designs/`, plans in `_harness/`, a `roadmap`, and a `progress` log — or in a named directive space at the `Agent/` root. No loose notes at the root. The operator-directives the agent follows (voice, conventions, preferences, feedback) are curated: the operator owns them, the agent refines them and reports the change.

**T3 (Memory) leaves** reuse the live entry convention — `<kind>/<slug>.md` with the locked frontmatter (below); **kind is the subfolder**, because kind drives recall. The reserved leading-underscore folders (`_always-load`, `_inbox`, `_archive`, `_idea-incubator`, `_meta`) are recall-aware. Two consolidation kinds are designed-for: `crystallized/` (the phase-close digest) and `procedural/` (distilled how-to) — see *How it grows*.

**Archive at every tier.** Each tier carries a cold `_archive/` for retired content. Recall **skips it by default**, so the record stays cheap to load as it grows; it opens only on deep research, an explicit ask, or granted permission. Nothing is hard-deleted — "prune" means *move to `_archive/`* (markdown stays the source of truth; the revert-log is the undo). The per-tier archive policy follows the autonomy gradient: **T1** archives only on an operator-confirmed proposal; **T2** archives a curated artifact when its successor supersedes it; **T3** the agent archives its own cold entries on its own. *Capture* describes the decay pipeline that feeds the archive.

### What a memory is — the entry contract

A memory is **one atomic entry**: a markdown note with a locked frontmatter block, stored under its kind's group. The frontmatter is what makes it addressable and curatable:

- **`kind`** — the classification above (routes storage, scopes recall).
- **`status`** — `active` / `superseded` — recall filters superseded entries out.
- **`always_load`** — the boot-injection gate: an entry with `always_load: true` is assembled into context at session start (**as-built, the gate is `_always-load/` directory membership** — the authoritative source; the field mirrors it); the **heat policy flips this field** as the entry heats up or cools (the [Experience design](agentm-experience-and-dreaming.md)).
- **`supersedes`** — a back-link to the entry this one replaces (the supersession chain — see Capture).
- plus `created` / `updated` / `tags` / `slug`.

The content rule is **engagement, not encyclopedia**: an entry captures *why it mattered here and how to apply it*, not a generic description. **One note is one fragment** — small, indexable on its own — and **densely linked to its neighbors**. Beyond the typed `supersedes:` back-link, a note cross-references related entries with Obsidian `[[wikilinks]]`, the native interconnection substrate; the engine keeps those links sound (link-integrity discovery across the corpus, `notes_link_discovery.py`). Linking is **first-class**: a well-connected fragment is what lets the vector index, and later the V6 knowledge-graph, retrieve over *relationships*, not just text.

### How storage is served — one port, every caller through it

Everything agentm remembers reaches disk through a single **storage port** — the seam. The discipline is load-bearing: *there is exactly one way to storage, and every caller goes through it.* Read bottom-to-top — three layers, each with one job:

1. **The storage seam — the one port.** The only layer that talks to a concrete store. `device-local` and `obsidian-vault` are interchangeable adapters; **more can be added** by implementing the same contract. Nothing above knows whether bytes land on the local filesystem or a synced vault — that ignorance is the point of a port.
2. **The memory engine — the one set of verbs.** `save` · `recall` · `forget` + the cross-cutting logic, every verb reaching the store *through* the seam.
3. **Inbound adapters — the ways in.** **In-process** (always present, a plain library call, zero daemon — the simple local case) and the **MCP server** (opt-in, a thin transport shim forwarding to the *same* engine).

![How storage is served, three layers: inbound adapters (in-process always-present, MCP server opt-in) reach the memory engine; the engine reaches the storage seam (selector + StorageBackend contract); the seam dispatches to interchangeable backends (device-local, obsidian-vault). Thick = always-present path, dotted = opt-in](diagrams/agentm-memory-storage-serving.svg)

The MCP server **belongs above the seam, as a client of it** — never underneath (that would force a daemon onto the simple local case and invert the layering; V5-0 put the mutex + content-hash CAS into the storage *primitives* precisely so the system needs no daemon). The seam, selector, device-local backend, and MCP shim are genericizable → **agentm substrate**; the `obsidian-vault` backend implements the contract one-way up → a **crickets backing plugin**.

The **MCP server** carries five load-bearing design choices:
- **Singleton streamable-HTTP** — one daemon, many sessions (`Mcp-Session-Id`), collapsing the host fan-out to a single writer alongside the CLI. Not stdio: stdio spawns one server per client, giving N OS processes on `vault_mutex` — safe but not single-writer.
- **Three snake_case tools**: `memory_search` · `memory_append` · `memory_forget`. Not dot-names: OpenAI-family MCP hosts reject them. (A fourth, `memory_recall`, was retired — R0.9, see the amendment log.)
- **Soft-delete**: `memory_forget` flips `status → deleted` + stamps `deleted_at`; the file is never unlinked. Not hard-delete: GDrive sync resurrects a hard-deleted file from propagation cache.
- **Loopback-first** (`127.0.0.1` / Unix socket): the remote tier (cross-device via OAuth 2.1 tunnel) is a deferred v1.1 addition.
- **Built on FastMCP, pinned `>=3,<4`** — with the official MCP SDK as a named fallback. Not unpinned: a FastMCP major can move the transport surface under the server.

**As-built vs. target.** Today harness *state* routes through the seam, but memory **entries** and the MCP server still write the vault directly — reaching around the port. Routing `save`/`recall`/`forget` through the engine→seam and re-platforming the MCP tools is **V5-14** (see References).

### Capture — the write protocol

The goal is concurrency-safe writes: when two sessions save to the same synced backend at once, both land and neither corrupts the other. A memory reaches disk through the V5-0 write protocol — a single `atomic_write` (temp → `fsync` → rename), guarded by an advisory **per-backend mutex** (a `mkdir` lock with an mtime heartbeat + stale-takeover, living *outside* the synced store) and, for replace-style files, a **content-hash compare-and-swap** that re-reads inside the lock before committing. Coordination lives in these *primitives*, not a daemon. *(Honest boundary: this is grounded at the seam — the `backend.write` body that composes these for the vault lives in the `obsidian-vault` plugin.)*

**Supersession is archive-then-replace, not overwrite.** A new entry that evolves an old one flips the old to `status: superseded` and sets a `supersedes:` back-link from the new — so the history is an **auditable link-chain**, not a lost update or a numeric confidence score.

**Archive, decay, and prune.** Supersession is one road into the archive; the other is **decay** — an entry that goes cold over time is retired to its tier's `_archive/` rather than left to bloat recall. Two arms run today: heat curation demotes a cold always-load entry to its group root, and `/memory evolve` archives a superseded entry. The fuller lifecycle — access-reinforced decay, consolidation tiers (episodic → semantic → procedural), the phase-close crystallization digest, and a whole-corpus dreaming pass that compacts supersession chains — is **designed-for**, framed in V6/V7. Two prerequisites gate any *autonomous* archive: the **revert-log plus a `derived-from` provenance edge** (so undoing a consolidated entry also undoes what was derived from it), and a **staging gate** — an autonomous job proposes archival to a staging inbox and the operator confirms via the digest before anything goes cold (`_dream-staging/`, the pre-approval inbox, is a separate place from `_archive/`, the cold store). Prune resolves to archive; there is no hard delete.

**Writing T1 (personal) takes a separate, explicit call.** The normal write path reaches T2/T3 inside `Agent/`. Personal content sits outside `vault_path`, so writing it goes through a distinct seam call that the operator's request authorizes — the line between agent-controlled and user-controlled space (the [memory-storage-seam design](memory-storage-seam.md)).

### Surface — the recall loop

Memory is injected by two hooks. At **session start**, the **always-load set** (entries gated by `always_load: true`) is assembled under a hard ~500 ms budget. On **every prompt**, a five-step engine runs under ~300 ms:

1. **tokenize** the prompt;
2. **embed** it (a local embedder, degrading to a deterministic stub — the API embedder mode was removed) and search the local sqlite-vec index for the top-K by cosine similarity;
3. **keyword-grep** (filtering `status: superseded`);
4. **merge** (semantic-weighted: ~0.85 similarity + ~0.05 keyword);
5. **dedup** against always-load, and return the top few.

Recall is **token-budgeted** and **scoped by kind + phase** — a phase pulls the kinds its budget allows, not a flat top-K over the whole corpus *(as-built today: a flat token-budgeted top-K; the `--kind`/`--phase` scoping is designed — see Risks)*. The vector index is **device-local and never synced** (the local-index tier), with mtime-vs-indexed-at drift detection that falls back to grep when an entry changed since it was embedded. Recall **skips every tier's `_archive/` by default** — the cold store stays out of the always-load floor and the per-prompt search, so a growing record does not inflate what each call pays. An `--include-archive` opt-in (mirroring `--include-inbox`) widens the walk over the archive for deep research, an explicit ask, or a granted request; the vector index still covers archived entries, so an opened search ranks them on the same budget.

### How it grows — into an interconnected knowledge base

The record is built to **compound**. Three things turn a growing pile of entries into a navigable knowledge base:

- **Learning populates it.** Reflection mines durable entries from finished sessions, and forward learning + deep research reach approved sources and the web for any task and bring back what's worth keeping — typed and filed like anything else (the [Experience design](agentm-experience-and-dreaming.md)). The more agentm works and reaches outward, the larger and richer the record. This is the growth engine.
- **It's interconnected by wikilinks — built.** Every entry cross-references its neighbors with `[[wikilinks]]`, and the engine maintains link integrity. The links are the connective tissue: the substrate everything else builds on.
- **It becomes navigable by an index over the markdown — built → designed-for.** Today a device-local vector index makes the corpus semantically searchable. The designed-for V6 layer extends this *without* moving the source of truth off markdown: a **knowledge-graph** (V6-2) extracts typed edges over the wikilinks (deterministic, no LLM) so relationships become a retrieval path — including **multi-hop traversal**; a **SQLite metadata table**, **chunking**, and **RRF hybrid retrieval** (V6-3/10/11) sharpen recall as the corpus grows past the hundreds. Graph and index are layers *over* the pages — markdown stays the source of truth; the graph is for navigation and discovery.

That is the trajectory toward a true knowledge base: a typed, densely-linked, indexed record that accumulates and compounds as agentm learns — the brain the other three pillars stand on. The typed entries, the wikilink substrate, and the vector index are built today; the knowledge-graph and the richer index are **designed-for, framed in V6** (Risks).

## Dependencies

- **The seam design** — [memory-storage-seam](memory-storage-seam.md) holds the deep storage contract (A2-fold, launched 2026-06-24); this pillar points down to it.
- **Experience grows the sole-owned space** — reflection, heat curation, dreaming write here ([Experience design](agentm-experience-and-dreaming.md)); the heat policy owns the `always_load` gate.
- **Opinions keep their learned half here** — an opinion's vault supplement is a memory entry ([Opinions design](agentm-opinions-and-gates.md)).
- **Personas draw their state here** — Memory is the pseudo-persona beneath all ([Personas design](agentm-personas.md)).
- **crickets backs the store** — `obsidian-vault` implements the `StorageBackend` contract, one-way up.
- **The runner + the digest** — scheduled jobs (the job-runner design, designed-for) write T2/T3 on their own and surface T2 changes to the operator through the reporting capability's digest; both route by the ownership tiers above.

## Risks & open questions

- **V6 indexed-recall is designed-for, not built.** The recall loop is the pre-V6 form (semantic-weighted merge + grep fallback); the reserved `DerivedMaintenance` extension point is where V6 adds the **knowledge-graph layer** (typed edges over the wikilinks; multi-hop traversal), **hybrid retrieval** (RRF over BM25 + vector + graph), a **SQLite metadata table**, **consolidation tiers**, and **chunking**. Graph and index stay layers over the markdown (the pages remain source of truth). Framed designed-for; the V6-11 metadata table is specified in the [memory index](agentm-memory-index) design, and the rest of the spec lives in the V6 plan + the seam design.
- **Kind-scoped recall — validate as-built.** The `kind` classification and the entry frontmatter are live; confirm the current `recall.py` actually enforces the kind/phase recall budget vs a flat top-K (a check at review).
- **The decay/archive lifecycle is mostly designed-for.** Heat curation + supersession-archive ship; access-reinforced decay, consolidation tiers, crystallization, self-healing lint, and the dreaming compaction pass are V6/V7, gated on the revert-log + the `derived-from` provenance edge and the staging inbox. Until those land, archival stays manual (`/memory evolve`) or operator-confirmed. The migration to the three-tier folder layout is operator-gated and updates the hardwired `personal/_always-load` constants in `recall.py` / `heat_policy.py` / `save.py`.
- **The seam content has migrated** — `memory-storage-seam` is launched (A2 ADR-fold, 2026-06-24); this pillar holds the pointer down to it.
- **The kernel `storage_vault.py` was deleted** — removed in V5-3 (commit d95468b); the vault backend now lives only in the `obsidian-vault` plugin's `storage_vault.py`.
- **Re-audit triggers:** confirm kind-scoped recall.

## References

- `scripts/harness_memory.py` — `vault_path()` resolver, config readers, `resolve_project()`, backend-aware `*_state_file`
- `scripts/storage_seam.py` + `scripts/backend_selection.py` — the seam contract + selector (deep detail in the seam design)
- `scripts/capability_resolver.py` — capability-availability (the `enhances:` runtime half)
- `scripts/vault_lock.py` — `atomic_write` · `content_hash` (CAS) · `vault_mutex` (the V5-0 primitives)
- `harness/skills/memory/scripts/` — `recall.py` (5-step engine), `vec_index.py` (sqlite-vec index), `save.py` / `evolve.py` (write + supersession), `notes_link_discovery.py` (wikilink integrity)
- `harness/hooks/` — `memory-recall-session-start`, `memory-recall-prompt-submit`
- [memory-storage-seam design](memory-storage-seam.md) — the concurrent-write protocol, seam fail-loud selection, V5-3 cutover, routing plane, backend-aware harness state
- V5-14 — storage-convergence (memory-entry seam adoption + MCP re-platform); ROADMAP-MASTER ⑤

## Amendment log

**2026-07-03 — retired the `memory_recall` MCP tool (R0.9 / agentmEngine#2).** `memory_recall` delegated to `harness_memory.phase_recall()`, which has returned `""` unconditionally for every call since the V5-3 vault-backend removal — dead in the sense that it always degraded, not in the sense that nothing called it. Verified no live crickets consumer actually called it (crickets' documenter sub-agent, the one consumer with a genuine live dependency on the recall machinery, calls `harness_memory.py`'s own `documenter-context` CLI verb instead — a separate surface this retirement did not touch, since it still has a real caller). `scripts/process_seam.py`'s `recall_here` (the seam-side wrapper around the same dead `phase_recall` call) was retired in the same commit for the same reason. The tool count in "How storage is served" above drops from four to three. Why not wire it to the live `recall.py` engine instead: that integration is R1.6's scope (the `verify-mcp-surface` suite, landing after the regression net); wiring a half-finished MCP tool now, before that net exists, risks a second silent-degrade bug shipping unnoticed. *Re-audit trigger:* if a future MCP consumer needs phase-scoped recall over the network transport, re-introduce `memory_recall` wired to the real engine as part of R1.6, not as a resurrection of the dead stub.

**2026-07-03 — dropped `scripts/harness_memory.py` from `governs:` (R0.10 / agTrack#0 governs-overlap fix).** This design and [memory-storage-seam](memory-storage-seam.md) both stamped an exact-path `governs: scripts/harness_memory.py`, so `governs_resolver.py` resolved the file as `{"governed": false, "reason": "overlap"}` — silently treated as greenfield by grounding Hooks 1/2, the exact failure mode the resolver's fail-loud-on-tie behavior exists to surface, not paper over. `memory-storage-seam` is the more specific, storage-plane-scoped owner (it already lists `harness_memory.py` alongside `backend_selection.py`/`storage_seam.py`/etc. as one cohesive seam); this design keeps the broader `harness/skills/memory/scripts/` directory pattern, which covers the engine-facing recall/save/reflect/vec_index surface `harness_memory.py` itself is not part of. Why not the reverse (keep it here, drop it from the seam design): the seam design's governs list is the storage-routing cohort — `harness_memory.py` is the seam's own resolve_project/vault_path entry point, so it belongs with its siblings, not orphaned into the broader memory-system pillar. *Re-audit trigger:* if `harness_memory.py` is ever split into a storage-facing half and an engine-facing half, re-evaluate whether the engine half should be re-added here.

**2026-06-28 — lock-down sweep (operator review).** Sized the three diagrams (`width`/`height`). Confirmed the **aging / relevance-decay / re-weighting** question is future work, not a gap — the lifecycle layer (confidence + retention decay, V6-1), consolidation tiers (V6-4), time-weighted retrieval (recency × relevance, V6-12), and dream-mode compaction (V7-2) are all queued; the designed-for framing here stands, and we hold modifying until V6/V7. Reordered this log newest-first (the 06-24 ADR-0007 fold had sat below the 06-21 authored entry). *No content change to the pillar.*

**2026-06-28 — W4 as-built reconciliation (paired with the foundations gate-count clarification).** Added half-sentence as-built caveats where live code differs from the designed target: the always-load gate is **`_always-load/` directory membership** (authoritative; the `always_load:` field mirrors it); the embedder chain is **local → stub** (the API embedder mode was removed); per-prompt recall is a **flat token-budgeted top-K** today (the `--kind`/`--phase` scoping stays designed — see Risks). `save.py`'s `group` is still an independent caller param (no kind→group derivation yet). The target shape is unchanged; these are honesty caveats per critique W4. *Re-audit triggers:* drop each caveat as `recall.py` / `embed.py` / `save.py` converge to the design.

**2026-06-26 — codified the three ownership tiers + the vault folder layout, the per-tier archive, and the decay pipeline (operator design pass).** Reworked §"By durability + ownership" into three explicit tiers by ascending autonomy — **T1 personal** (the operator's vault above `Agent/`, written only through a separate explicit seam call), **T2 curated/collaborative** (the `Agent/` shared drive: designs · plans · roadmaps + the operator-directives; autonomous writes reported in the digest, revertable), **T3 agentm-sole** (`Agent/Memory/`, fully autonomous) — reconciling the prior "co-owned"/"user-owned"/"agentm-sole" band labels. Added the opinionated vault layout (the T2 `projects/<project>/` shape + the prescribed `Memory/` internals: `<kind>/<slug>.md` leaves, the reserved folders, a generated `_index.md`, the `crystallized`/`procedural` consolidation kinds), an `_archive/` at every tier with recall skipping it by default (the `--include-archive` opt-in), and the archive/decay/prune lifecycle (heat curation + supersession ship; the fuller decay → consolidation → crystallization → dreaming-compaction arc is designed-for, gated on the revert-log + provenance edge + a staging inbox; prune resolves to archive, never hard delete). Why not approve-before for curated content: the operator co-owns it, so autonomous-write-with-revert + a digest report is the lighter, correct gate; a non-revertable curated change is the one case that would warrant asking, accepted as a known limitation until agent-memory is git-backed (backlogged). Grounded in the 2026-06 research (R02 lifecycle/consolidation · R06 token-efficiency · R09 content-shapes) + the live `save.py`/`recall.py`/`evolve.py`/`heat_policy.py`. **Re-audit triggers:** flip the decay/consolidation arms to as-built as V6/V7 land; run the operator-gated vault migration (relocate today's `Agent/personal/*` learned-memory → `Memory/`, directives → T2, updating the hardwired `personal/_always-load` constants); confirm the new `reporting` capability's digest is the T2-change report surface.

**2026-06-24 — folded ADR 0017 (MCP server) into §“How storage is served” (AG ADR-migration tail).** Five DC calls, all surfaced into the body: singleton streamable-HTTP + broker property (DC-1); 4 snake_case tools `memory_search · memory_recall · memory_append · memory_forget` (DC-2, dot-names break OpenAI-family hosts); soft-delete `status → deleted` + `deleted_at` never unlinks (DC-3, not hard-delete: GDrive sync resurrects from propagation cache); loopback-first / deferred remote tier via OAuth 2.1 (DC-4); FastMCP `>=3,<4` primary / official SDK named fallback (DC-5). *Re-audit triggers:* MCP spec deprecates streamable-HTTP; spec mandates dot-names; next FastMCP major; hard-delete obligation arises; homelab posture changes.

**2026-06-24 — folded ADR 0007 into this design (AG Phase 4, move-and-retire).**

**0007 — Auto-context into harness phases (2026-05-22; amended 2026-05-27 / 2026-05-28 / 2026-05-31).** `harness_memory.py` injects recalled memory into each phase at session start. Five design calls: (Q1) per-phase recall budgets to prevent context bloat; (Q2) three-tier slug detection for kind / status / tags; (Q3) graceful-skip if the vault is unavailable; (Q4) confidence-modulated ask before injecting low-confidence entries; (Q5) dual-trigger `progress.md` (phase-end + explicit save). Amended 2026-05-27: memory files moved to `agentm/harness/skills/memory/`. Amended 2026-05-28: documenter recall phase added. Amended 2026-05-31: SessionStart hook vault-path resolution order — `$MEMORY_VAULT_PATH` env var → `.agentm-config.json::vault_path` → no vault (graceful skip). Why not always-load all entries: per-phase budgets prevent context bloat; graceful skip ensures vault unavailability never breaks a phase. Why not single-trigger `progress.md`: dual-trigger ensures progress is written even if the phase crashes mid-run. *Re-audit trigger:* when V6 indexed-recall lands, re-examine whether per-phase budgets are still the right token-control knob; if dual-trigger causes duplicate entries, consolidate to a single write-and-flush.

**2026-06-21 — authored, reviewed, and finalized.**

Migrated from the agentm HLD, deepened against the live code, and conformed to the abbreviated-design template (Objective / Overview / Design / Dependencies / Risks) with all three diagrams. Documents the Memory pillar: a substrate where **everything points inward** (engine → resolution → seam), classified on **three axes** — **kind** (routes storage + scopes recall), durability, and ownership — with each memory an **atomic frontmatter-keyed entry** (`kind` / `status` / `always_load` / `supersedes`; engagement-not-encyclopedia; one-note-one-fragment). Capture is concurrency-safe and **archive-then-replace** (a supersession link-chain); recall is the kind-scoped, token-budgeted five-step loop over a device-local, never-synced index.

Operator-approved after **restoring the content-first axis** (the `kind` taxonomy + the entry contract) the storage-first reframe had dropped. Content-final; `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3). **Designed-for, not built:** the V6 indexed-recall work (hybrid RRF · knowledge-graph · consolidation tiers · chunking) under the `DerivedMaintenance` reservation, and V5-14 storage-convergence (memory entries + the MCP server still reach around the seam today). **Re-audit triggers:** flip the V5-14 as-built flags when convergence lands; migrate the seam prose into `memory-storage-seam` at the A2 fold; confirm `recall.py` enforces the kind/phase recall budget (vs flat top-K).

**2026-06-21 — reopened (living-design amendment): backlinking made first-class + the interconnection/brain trajectory named (operator).** The design implied the "grows into a knowledge base" direction but never stated it. Made Obsidian `[[wikilinks]]` first-class in the entry contract (link-integrity discovery, `notes_link_discovery.py` — built), added a *How it grows* design subsection tying the growth engine (forward learning + deep research → typed entries), the wikilink substrate (built), and the index-over-markdown (vector built → V6 knowledge-graph + metadata table + chunking + RRF designed-for) into one trajectory, and stated the trajectory in the Objective. Why not leave it implicit: the operator expects the store to grow into a true brain of knowledge, and the design should say so and name the interconnection path, not leave it to inference. The V6 graph/index stay framed designed-for (no source-attribution; markdown remains source of truth). **Re-audit trigger:** when V6-2 lands, flip the knowledge-graph from designed-for to as-built here.
