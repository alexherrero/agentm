---
title: memory-system — design
status: launched
kind: design
scope: feature
area: agentm/memory
governs: [scripts/harness_memory.py, harness/skills/memory/scripts/]
parent: agentm-hld.md
seeded: 2026-06-20
approved: 2026-06-21
---

> [!NOTE]
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21).** child-design — the Memory pillar, parent [agentm HLD](agentm-hld.md); inherits the [Foundations HLD](agentm-foundations-hld.md) by reference. The largest pillar — the ground the other three stand on. `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3); the seam content has migrated to the launched `memory-storage-seam` design.

# AgentM Memory System

## Objective

Memory is the **durable record agentm keeps** — what it has learned, the plans and designs it works on, the standards it holds — and the single disciplined path every caller takes to reach it. It is the largest of the four pillars and the ground the other three stand on: **Experience** grows it, **Opinions** keep their learned half in it, **Personas** draw their state from it.

It is built to **compound**, not just persist: entries are typed, densely linked to their neighbors, and indexed for recall — so as agentm learns and reaches outward, the record grows into an interconnected knowledge base, not a flat log.

## Overview

The substrate serves one aim: **every caller reaches storage the same way, and nothing leaks a dependency downward.** A small set of generic pieces — none knowing a specific backend, host, or tool — arranged so everything points *inward*: a caller reaches the memory engine, the engine reaches storage through the resolution plane and the seam, and the concrete backends, personas, and crickets tools all depend *up*. The substrate depends on nothing below it.

![Whole-component view: in-process callers, MCP clients (opt-in), crickets tools, and personas all depend on the memory engine; the engine reaches the resolution plane then the storage seam; the device-local and obsidian-vault backends implement the seam — every arrow points inward to the substrate](diagrams/agentm-memory-whole-component.svg)

The components:

- **The memory engine** — the verbs the rest of the system calls (`save` · `recall` · `forget`, plus reflection) and the cross-cutting logic that must live exactly once: idempotency + content-hash CAS, soft-delete, token-budgeted recall, link integrity.
- **The resolution plane** — how a call finds its store without naming one: the config holds the backend choice; the selector (`backend_selection.py`) maps a protocol name to a concrete `StorageBackend`, failing loud if it's missing.
- **The storage seam** — the one port to disk: a `StorageBackend` contract, a registry, an opaque `Locator`, and the storage tiers. *(The seam's full contract — the verbs, the `Locator` guards, the tiers + the never-sync invariant, the reserved `DerivedMaintenance` — is the launched [memory-storage-seam design](memory-storage-seam.md); this pillar points down to it.)*
- **Harness-state I/O** — plan/progress/feature state is backend-aware (ADR 0020): it routes through the seam to the active backend, so state and memory reach disk the same way.

**The one-way rule, enforced.** Routing and memory code may import the seam + selector (substrate) but **never a concrete backend** — the LC-8 gate (`check-process-seam-import-direction.sh`) fails the build on any `import storage_vault`. The backend is chosen at call time and injected through the abstract contract. Backends and tools point up; the substrate points at nothing below.

## Design

### How memory is classified — three axes

agentm classifies every memory on **three independent axes**: by **kind** (what sort of thing it is), by **durability** (does it survive the session?), and by **ownership** (whose space is it?). Kind drives *retrieval*; durability and ownership drive *placement*.

**By kind.** Every entry declares a **`kind`** — open-ended but conventional: `preference` · `workflow` · `fix` · `domain-reference` · `idea` · `skill-pointer`. Kind is load-bearing: it picks the storage group an entry is written to, and it scopes which entries a phase pulls into its recall budget (a `/work` pass recalls different kinds than a `/plan` pass). Kind is how a caller asks for *the right sort of memory* rather than a flat search over everything.

**By durability + ownership.** Two planes, shown below:

- **Layer 0 — agent-harness memory (not durable):** the host's context window — the running conversation. Short-lived; it fills and gets summarized away. Scratch space; the record lives below, on disk. *(The layer the whole substrate exists to compensate for.)*
- **Layer 1 — the agentm substrate (durable):** the on-disk record, resolved at runtime (never a cached path). Within it, three **ownership bands**: **agentm-sole** (memories, learnings, reflection — agentm writes/curates/prunes freely; the part that makes "gets better over time" true), **co-owned with the user** (plans, designs, research — collaborative; agentm drafts, the human ratifies), and **user-owned** (the vault *above* the Agent folder — agentm reads and links but writes **only when told**).

![The layers of memory: Layer 0 harness memory (ephemeral scratch) above the durability line; Layer 1 the durable substrate below it, split into agentm-sole and co-owned-with-the-user; and below the Agent-folder boundary, the user's curated vault that agentm reads but writes only when told](diagrams/agentm-memory-layers.svg)

*The diagram shows the durability × ownership planes; **kind is the orthogonal third axis**, carried in each entry's frontmatter.*

### What a memory is — the entry contract

A memory is **one atomic entry**: a markdown note with a locked frontmatter block, stored under its kind's group. The frontmatter is what makes it addressable and curatable:

- **`kind`** — the classification above (routes storage, scopes recall).
- **`status`** — `active` / `superseded` — recall filters superseded entries out.
- **`always_load`** — the boot-injection gate: an entry with `always_load: true` is assembled into context at session start; the **heat policy flips this field** as the entry heats up or cools (the [Experience design](agentm-experience-and-dreaming.md)).
- **`supersedes`** — a back-link to the entry this one replaces (the supersession chain — see Capture).
- plus `created` / `updated` / `tags` / `slug`.

The content rule is **engagement, not encyclopedia**: an entry captures *why it mattered here and how to apply it*, not a generic description. **One note is one fragment** — small, indexable on its own — and **densely linked to its neighbors**: beyond the typed `supersedes:` back-link, a note cross-references related entries with Obsidian `[[wikilinks]]`, the native interconnection substrate, and the engine keeps those links sound (link-integrity discovery across the corpus, `notes_link_discovery.py`). Linking is **first-class, not decoration** — a well-connected fragment is what lets the vector index, and later the V6 knowledge-graph, retrieve over the *relationships* and not just the text.

### How storage is served — one port, every caller through it

Everything agentm remembers reaches disk through a single **storage port** — the seam. The discipline is load-bearing: *there is exactly one way to storage, and every caller goes through it.* Read bottom-to-top — three layers, each with one job:

1. **The storage seam — the one port.** The only layer that talks to a concrete store. `device-local` and `obsidian-vault` are interchangeable adapters; **more can be added** by implementing the same contract. Nothing above knows whether bytes land on the local filesystem or a synced vault — that ignorance is the point of a port.
2. **The memory engine — the one set of verbs.** `save` · `recall` · `forget` + the cross-cutting logic, every verb reaching the store *through* the seam.
3. **Inbound adapters — the ways in.** **In-process** (always present, a plain library call, zero daemon — the simple local case) and the **MCP server** (opt-in, a thin transport shim forwarding to the *same* engine).

![How storage is served, three layers: inbound adapters (in-process always-present, MCP server opt-in) reach the memory engine; the engine reaches the storage seam (selector + StorageBackend contract); the seam dispatches to interchangeable backends (device-local, obsidian-vault). Thick = always-present path, dotted = opt-in](diagrams/agentm-memory-storage-serving.svg)

The MCP server **belongs above the seam, as a client of it** — never underneath (that would force a daemon onto the simple local case and invert the layering; V5-0 put the mutex + content-hash CAS into the storage *primitives* precisely so the system needs no daemon). The seam, selector, device-local backend, and MCP shim are genericizable → **agentm substrate**; the `obsidian-vault` backend implements the contract one-way up → a **crickets backing plugin**.

**As-built vs. target.** Today harness *state* routes through the seam, but memory **entries** and the MCP server still write the vault directly — reaching around the port. Routing `save`/`recall`/`forget` through the engine→seam and re-platforming the MCP tools is **V5-14**; flip these flags when it lands.

### Capture — the write protocol

The goal is concurrency-safe writes: when two sessions save to the same synced backend at once, both land and neither corrupts the other. A memory reaches disk through the V5-0 write protocol — a single `atomic_write` (temp → `fsync` → rename), guarded by an advisory **per-backend mutex** (a `mkdir` lock with an mtime heartbeat + stale-takeover, living *outside* the synced store) and, for replace-style files, a **content-hash compare-and-swap** that re-reads inside the lock before committing. Coordination lives in these *primitives*, not a daemon. *(Honest boundary: this is grounded at the seam — the `backend.write` body that composes these for the vault lives in the `obsidian-vault` plugin.)*

**Supersession is archive-then-replace, not overwrite.** A new entry that evolves an old one flips the old to `status: superseded` and sets a `supersedes:` back-link from the new — so the history is an **auditable link-chain**, not a lost update or a numeric confidence score.

### Surface — the recall loop

Memory is injected by two hooks. At **session start**, the **always-load set** (entries gated by `always_load: true`) is assembled under a hard ~500 ms budget. On **every prompt**, a five-step engine runs under ~300 ms: **tokenize** → **embed** (an API embedder, degrading to a local model, then a deterministic stub) and search the local sqlite-vec index for the top-K by cosine similarity → **keyword-grep** (filtering `status: superseded`) → **merge** (semantic-weighted: ~0.85 similarity + ~0.05 keyword) → **dedup** against always-load → return the top few. Recall is **token-budgeted** and **scoped by kind + phase** — a phase pulls the kinds its budget allows, not a flat top-K over the whole corpus. The vector index is **device-local and never synced** (the local-index tier), with mtime-vs-indexed-at drift detection that falls back to grep when an entry changed since it was embedded.

### How it grows — into an interconnected knowledge base

The record is built to **compound**, not just hold. Three things turn a growing pile of entries into a navigable knowledge base:

- **It's populated by learning, not only by hand.** Reflection mines durable entries from finished sessions, and forward learning + deep research reach approved sources and the web for any task and bring back what's worth keeping — typed and filed like anything else (the [Experience design](agentm-experience-and-dreaming.md)). The more agentm works and reaches outward, the larger and richer the record. This is the growth engine.
- **It's interconnected by wikilinks — built.** Every entry cross-references its neighbors with `[[wikilinks]]`, and the engine maintains link integrity. The links are the connective tissue: the substrate everything else builds on.
- **It becomes navigable by an index over the markdown — built → designed-for.** Today a device-local vector index makes the corpus semantically searchable. The designed-for V6 layer extends this *without* moving the source of truth off markdown: a **knowledge-graph** (V6-2) extracts typed edges over the wikilinks (deterministic, no LLM) so relationships become a retrieval path — including **multi-hop traversal**; a **SQLite metadata table**, **chunking**, and **RRF hybrid retrieval** (V6-3/10/11) sharpen recall as the corpus grows past the hundreds. Graph and index are layers *over* the pages — markdown stays the source of truth; the graph is for navigation and discovery.

That is the trajectory toward a true knowledge base: a typed, densely-linked, indexed record that accumulates and compounds as agentm learns — the brain the other three pillars stand on. The typed entries, the wikilink substrate, and the vector index are built today; the knowledge-graph and the richer index are **designed-for, framed in V6** (Risks).

## Dependencies

- **The seam design** — [memory-storage-seam](memory-storage-seam.md) holds the deep storage contract (A2-fold, launched 2026-06-24); this pillar points down to it.
- **Experience grows the sole-owned space** — reflection, heat curation, dreaming write here ([Experience design](agentm-experience-and-dreaming.md)); the heat policy owns the `always_load` gate.
- **Opinions keep their learned half here** — an opinion's vault supplement is a memory entry ([Opinions design](agentm-opinions-and-gates.md)).
- **Personas draw their state here** — Memory is the pseudo-persona beneath all ([Personas design](agentm-personas.md)).
- **crickets backs the store** — `obsidian-vault` implements the `StorageBackend` contract, one-way up.

## Risks & open questions

- **V6 indexed-recall is designed-for, not built.** The recall loop is the pre-V6 form (semantic-weighted merge + grep fallback); the reserved `DerivedMaintenance` extension point is where V6 adds the **knowledge-graph layer** (typed edges over the wikilinks; multi-hop traversal), **hybrid retrieval** (RRF over BM25 + vector + graph), a **SQLite metadata table**, **consolidation tiers**, and **chunking**. Graph and index stay layers over the markdown (the pages remain source of truth). Framed designed-for; the spec lives in the V6 plan + the seam design.
- **Kind-scoped recall — validate as-built.** The `kind` classification and the entry frontmatter are live; confirm the current `recall.py` actually enforces the kind/phase recall budget vs a flat top-K (a check at review).
- **The seam content has migrated** — `memory-storage-seam` is launched (A2 ADR-fold, 2026-06-24); this pillar holds the pointer down to it.
- **The kernel `storage_vault.py` was deleted** — removed in V5-3 (commit d95468b); the vault backend now lives only in the `obsidian-vault` plugin's `storage_vault.py`.
- **Re-audit triggers:** flip the V5-14 as-built flags when storage-convergence lands; confirm kind-scoped recall.

## References

- `scripts/harness_memory.py` — `vault_path()` resolver, config readers, `resolve_project()`, backend-aware `*_state_file`
- `scripts/storage_seam.py` + `scripts/backend_selection.py` — the seam contract + selector (deep detail in the seam design)
- `scripts/capability_resolver.py` — capability-availability (the `enhances:` runtime half)
- `scripts/vault_lock.py` — `atomic_write` · `content_hash` (CAS) · `vault_mutex` (the V5-0 primitives)
- `harness/skills/memory/scripts/` — `recall.py` (5-step engine), `vec_index.py` (sqlite-vec index), `save.py` / `evolve.py` (write + supersession), `notes_link_discovery.py` (wikilink integrity)
- `harness/hooks/` — `memory-recall-session-start`, `memory-recall-prompt-submit`
- ADRs — 0012 (write protocol), 0013 (seam fail-loud selection), 0018 (V5-3 cutover), 0019 (routing-plane de-vaulting), 0020 (backend-aware harness state)
- V5-14 — storage-convergence (memory-entry seam adoption + MCP re-platform); ROADMAP-MASTER ⑤

## Amendment log

**2026-06-24 — folded ADR 0017 (MCP server design) into this design (AG ADR-migration tail, move-and-retire).** The held ADR 0017 resolved (design-doc amendment 2026-06-24) and folds into this design's **MCP-server inbound adapter** (§"How storage is served" — the opt-in transport shim above the seam; the in-process library call stays the always-present path). The V5-9 MCP server (`memory_mcp_server.py`) is the memory engine's network-reachable front door.

**0017 — MCP server: singleton-HTTP broker, four tools, loopback-first (2026-06-17).** Five load-bearing calls: **[DC-1]** one **singleton streamable-HTTP daemon**, many sessions (N `Mcp-Session-Id` headers) — every MCP-host write funnels through one process, the Phase-1 concurrent-write **broker** the V5-0 write protocol was staged to receive (collapsing the host fan-out from N writers to one, alongside the CLI). *Why not stdio:* it spawns one server per client, so N hosts are N OS processes contending on `vault_mutex` — the lock makes that safe but not single-writer; the broker property is the reason for the choice. **[DC-2]** **four snake_case tools** — `memory_search` · `memory_recall` · `memory_append` · `memory_forget`. *Why not dots* (`memory.search`): OpenAI-family hosts reject them — a name that breaks on a major host is not a name. *Why not fewer:* three would drop soft-delete (a status flip on an existing entry, not a write). *Why not more:* twenty-plus is the bloat target; `memory_get` is the named v1.1 verb, held for the remote tier. **[DC-3]** **soft-delete is a hard acceptance criterion** — `memory_forget` flips `status → deleted` + stamps `deleted_at`; the file is never unlinked. *Why not hard-delete:* the GDrive-synced vault resurrects an `unlink` from a cached copy during propagation (a status flip propagates as a content update, handled correctly); it also preserves the audit trail + un-delete, and matches the reference state machine. **[DC-4]** **loopback-first** (`127.0.0.1`, Unix socket where supported); the remote tier (claude.ai / cross-device) is a deferred v1.1 outbound-only tunnel (Tailscale → NetBird → Cloudflare) pulling in OAuth 2.1. *Why not build it now:* the homelab posture forbids port-forwards / public DNS / VPS; the loopback daemon already delivers the headline value (vault reachable from every desktop MCP host); the remote shape is bounded so it slots on additively. **[DC-5]** **FastMCP `>=3,<4`** primary, the official `mcp` SDK `<2` named fallback. *Why not the SDK primary:* at design time it lacked bearer-auth OOtB + an in-memory test client that fits `check-all.sh`'s offline discipline; the four-tool surface keeps the framework cheap to swap (the transport, DC-1, is the load-bearing choice). *Re-audit triggers:* the MCP spec deprecates streamable-HTTP; a spec revision mandates dot-names; the next FastMCP major (re-check whether the SDK closed the bearer-auth / in-memory gaps); a backend or compliance rule introduces a hard-delete obligation; the operator's homelab posture changes (re-rank the tunnel). *(V5-14 note: the MCP server still writes the vault directly today, reaching around the seam — re-platforming it onto the engine→seam path is tracked as V5-14.)*

**2026-06-21 — authored, reviewed, and finalized.**

Migrated from the agentm HLD, deepened against the live code, and conformed to the abbreviated-design template (Objective / Overview / Design / Dependencies / Risks) with all three diagrams. Documents the Memory pillar: a substrate where **everything points inward** (engine → resolution → seam), classified on **three axes** — **kind** (routes storage + scopes recall), durability, and ownership — with each memory an **atomic frontmatter-keyed entry** (`kind` / `status` / `always_load` / `supersedes`; engagement-not-encyclopedia; one-note-one-fragment). Capture is concurrency-safe and **archive-then-replace** (a supersession link-chain); recall is the kind-scoped, token-budgeted five-step loop over a device-local, never-synced index.

Operator-approved after **restoring the content-first axis** (the `kind` taxonomy + the entry contract) the storage-first reframe had dropped. Content-final; `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3). **Designed-for, not built:** the V6 indexed-recall work (hybrid RRF · knowledge-graph · consolidation tiers · chunking) under the `DerivedMaintenance` reservation, and V5-14 storage-convergence (memory entries + the MCP server still reach around the seam today). **Re-audit triggers:** flip the V5-14 as-built flags when convergence lands; migrate the seam prose into `memory-storage-seam` at the A2 fold; confirm `recall.py` enforces the kind/phase recall budget (vs flat top-K).

**2026-06-24 — folded ADR 0007 into this design (AG Phase 4, move-and-retire).**

**0007 — Auto-context into harness phases (2026-05-22; amended 2026-05-27 / 2026-05-28 / 2026-05-31).** `harness_memory.py` injects recalled memory into each phase at session start. Five design calls: (Q1) per-phase recall budgets to prevent context bloat; (Q2) three-tier slug detection for kind / status / tags; (Q3) graceful-skip if the vault is unavailable; (Q4) confidence-modulated ask before injecting low-confidence entries; (Q5) dual-trigger `progress.md` (phase-end + explicit save). Amended 2026-05-27: memory files moved to `agentm/harness/skills/memory/`. Amended 2026-05-28: documenter recall phase added. Amended 2026-05-31: SessionStart hook vault-path resolution order — `$MEMORY_VAULT_PATH` env var → `.agentm-config.json::vault_path` → no vault (graceful skip). Why not always-load all entries: per-phase budgets prevent context bloat; graceful skip ensures vault unavailability never breaks a phase. Why not single-trigger `progress.md`: dual-trigger ensures progress is written even if the phase crashes mid-run. *Re-audit trigger:* when V6 indexed-recall lands, re-examine whether per-phase budgets are still the right token-control knob; if dual-trigger causes duplicate entries, consolidate to a single write-and-flush.

**2026-06-21 — reopened (living-design amendment): backlinking made first-class + the interconnection/brain trajectory named (operator).** The design implied the "grows into a knowledge base" direction but never stated it. Made Obsidian `[[wikilinks]]` first-class in the entry contract (link-integrity discovery, `notes_link_discovery.py` — built), added a *How it grows* design subsection tying the growth engine (forward learning + deep research → typed entries), the wikilink substrate (built), and the index-over-markdown (vector built → V6 knowledge-graph + metadata table + chunking + RRF designed-for) into one trajectory, and stated the trajectory in the Objective. Why not leave it implicit: the operator expects the store to grow into a true brain of knowledge, and the design should say so and name the interconnection path, not leave it to inference. The V6 graph/index stay framed designed-for (no source-attribution; markdown remains source of truth). **Re-audit trigger:** when V6-2 lands, flip the knowledge-graph from designed-for to as-built here.
