# Memory-OS architecture: agentm as a storage-agnostic kernel + plugin host

> [!NOTE]
> **Status:** initial publication (v0.1) — 2026-06-03. The **V5 design pass**.
> **Position in arc:** **V5 of the Agent Memory evolution — "the unbundling."** Companion to [`agent-memory-evolution.md`](agent-memory-evolution.md) (the V1→V7 arc) and [`device-wide-architecture.md`](device-wide-architecture.md) (V4, device-wide). This doc locks the V5 shape: **agentm becomes a pure memory OS + plugin host**; every non-memory capability unbundles into a crickets plugin; storage becomes pluggable.
> **Lifecycle:** updated per the operator's [[hld-evolution-update-on-major-release]] convention — a new dated subsection per qualifying release. See **Lifecycle** at the bottom.

## Goals

Agent M's V4 made the harness **device-wide** — installed once per machine, present in every conversation, with state living in the operator's vault. But V4 also left agentm carrying *everything*: the memory engine, the phase-gated engineering workflow, a documentation system, GitHub-Projects/meta-loop integration, release tooling, and a bespoke multi-host installer — all fused into one repo. The memory engine was one tenant among many.

V5 cleaves that. **agentm becomes a storage-agnostic memory OS + plugin host.** It keeps only (1) the memory engine and (2) how it installs itself, hosts plugins, spans agents, and keeps itself healthy. Everything else — how it *works*, *documents itself*, *manages projects*, and *where it stores memory* — becomes a capability agentm gains by **installing a crickets plugin, and dogfoods on itself.**

This design locks the V5 shape:

1. **Two engines, one kernel.** agentm = the memory engine + the plugin-host substrate. The opinionated workflow, docs, PM, and storage backings are *not* in the kernel.
2. **Storage-agnostic core.** The memory engine persists through a **storage-backend seam**. With no backing plugin installed, it uses **device-level storage** (user-wide, whole-device, `~/.agentm/memory/`). Other backings — the Obsidian vault, anything else — are plugins.
3. **Capabilities unbundle into crickets plugins** — `engineering-process`, a wiki/documentation plugin, a project-management plugin, an `obsidian-vault` storage plugin — each generated as a native host plugin by the V3 generator ([`crickets-v3-native-plugins.md`](https://github.com/alexherrero/crickets/wiki/crickets-v3-native-plugins)).
4. **agentm dogfoods crickets to develop agentm.** The kernel's *runtime* (memory) stands alone; its *development loop* (work / document / manage) is entirely plugin-supplied. You need crickets plugins to develop anything in agentm.
5. **The operator's vault is conserved.** V5 does not demote anyone's memory. The Obsidian-vault-backed setup migrates intact via a **live cutover** (expand → parallel-run → contract), never a flag day.

V5 sequences *before* the former V5 (indexed retrieval, now **V6**) and the former V6 (dreaming, now **V7**) — the arc shifts right to make room, because retrieval sophistication and dreaming both assume a settled kernel/plugin boundary to build on.

## Background

Three things make V5 possible *now*, and one thing makes it necessary:

- **The native-plugin generator launched** (crickets v3.0.0). `src/<group>/` → `generate.py` → committed `dist/<host>/` native plugins for Claude Code + Antigravity. The composition vocabulary (`requires:` / `standalone:`) already exists. The bespoke v2 installer was retired ([crickets ADR 0014](../decisions/0014-install-decoupling.md)). Capabilities now *have somewhere clean to land* as native plugins.
- **The agentm↔crickets Project surface split landed** ([crickets ADR 0016](../decisions/0016-project-surface-split.md) / agentm ADR 0008).
- **A boundary analysis** partitioned agentm's surface and confirmed the cleave is feasible: the dependency arrow already points the right way — **memory never imports the workflow** — and every phase spec already ships graceful-skip-without-vault blocks.
- **It realizes the ROADMAP's own intent** — *"lift the tracks that were tangled inside V4 out, which is what shrinks the core back to its true self."*

### Relationship to V4 — a boundary redraw, and a generalization

V4 #36 (the V4.2 reorg) moved compound skills + the memory stack **into** agentm, drawing the agentm/crickets line at *compound-vs-base*. **V5 redraws that line at *memory-vs-everything-else*** — the non-memory compound capabilities move *back out* to crickets, now as native plugins rather than installed primitives.

V4 also locked **vault-as-canonical-context**: state lives in the vault, the vault is canonical by default. **V5 generalizes that principle rather than abandoning it.** For the operator, vault-canonical is fully conserved (they install the `obsidian-vault` plugin; their vault stays the live, authoritative store). What changes is that the *kernel* no longer hardwires the vault — "your *configured storage backing* is canonical" is the general form, and backing-the-memory-with-an-Obsidian-vault becomes a deliberate, pluggable choice. The V4 HLD's vault-canonical statement and the `[[vault-as-canonical-context]]` always-load convention are amended accordingly (see those docs' lifecycle logs).

## Architectural principles

### The partition test

> **Keep in the kernel only if it is (1) the storage-agnostic memory engine, or (2) how agentm installs itself / hosts plugins / spans agents / diagnoses + updates itself.** Everything else is a capability agentm gains by installing a crickets plugin.

### Storage-agnostic core; device-local default; backings are plugins

The memory engine's recall/reflect logic is storage-agnostic. Persistence happens through a small **storage-backend interface**. The kernel ships one built-in backend — `device-local` (`~/.agentm/memory/`, plain markdown, user-wide) — and that is the default when no storage plugin is present. The Obsidian vault is a *backend implementation shipped by the `obsidian-vault` plugin*, not a kernel hardwire.

### Fail loud, never silently demote

Storage selection (`storage.backend`) is honored strictly: if config names a backend whose plugin is **not** installed, the kernel **refuses memory operations with a clear "install the backend plugin" error** — it never silently falls back to device-local. Silent demotion is the one failure that could orphan or mis-write the operator's vault, so the seam forbids it. *This rule is what conserves the operator's configuration.*

### Dogfooding — agentm's dev capabilities are plugins

agentm's *runtime* (memory) has no dev-time dependencies. agentm's *development* hard-depends on crickets: it runs the wiki plugin's checks in CI, develops itself with `engineering-process`, and documents itself with the wiki plugin. The kernel cannot lint its own docs without installing a plugin. This is a deliberate, clean separation of runtime-deps from dev-deps.

### Operator-in-the-loop preserved

Carried from V4: device-wide ≠ autonomous. Capability plugins propose; the operator approves. Storage cutover surfaces state to the operator; no silent overwrites.

## Architecture

### What stays — the kernel

| Group | Components |
|---|---|
| **Memory engine** *(storage-agnostic)* | `memory` skill, `harness_memory.py` recall/reflect core, the memory recall+reflect hooks, `adapt-evaluator` + `memory-idea-researcher` sub-agents, the storage-backend interface, the `device-local` backend, memory tests, branding |
| **Routing / index / config mechanisms** | `repo_registry` (the OS map of repos), repo→slug resolution (formerly `vault_project`, de-vaulted), `detect_project` (the "configure a repo" engine), `project_config` (per-repo capability enablement), `agentm_config` (device install config) — all **de-vaulted onto the storage seam** |
| **Plugin host + cross-platform substrate** | `install.{sh,ps1}`, `lib/install/{bash,pwsh,python}`, `adapters/{claude-code,antigravity}`, `install-plugin.sh`, `smoke-install`, `sync-lib`, `validate-adapters`, the plugin scaffold — *modified as capabilities leave, but the kernel keeps the host* |
| **Diagnose + update self-care** | `doctor`, `agentm-update`, `telemetry` |

### What unbundles — capabilities → crickets plugins

| Capability | Destination | Notes |
|---|---|---|
| **Engineering process** | `engineering-process` plugin | phases, pipelines, `evidence-tracker` + `harness-context` hooks, adversarial-reviewer ×2, explorer, the design skill, cross-review, `ship-release` |
| **Wiki / documentation** | wiki plugin | `diataxis-author`, `wiki-author`, `migrate-to-diataxis`, the `documenter` agent, `check-wiki`, `recent-wiki-changes`, the wiki scaffold — **and maintaining agentm's own wiki** |
| **Project management / meta-loop** | PM plugin | GitHub-Projects integration + the cross-project briefing/nudge half of the orchestration engine |
| **Obsidian-vault storage** | `obsidian-vault` plugin | the vault backend, vault detection (`vault_probe`), Drive sync-conflict handling (`conflict-merger`), the `_index.md` convention, the consolidated `migrate-harness-to-vault-backed` skill, and the `vault_path` config it adopts on first run |

The fused **`auto_orchestration`** engine splits three ways: reflection cadence → kernel; per-repo phase-dispatch → `engineering-process`; cross-project briefing/nudges → PM plugin. (This is a real refactor, not a file-move — tracked as its own task.)

### The two seams

V5 introduces a second seam. Both are thin contracts the kernel exports; capability plugins are clients.

- **`memory ↔ process`** — agentm exports a read-only, **graceful-no-op** memory API (`recall-here` / `offer-save-here` / `state-path`) that the engineering-process plugin calls and degrades cleanly without. Memory never imports the process.
- **`memory ↔ storage`** *(new)* — the storage-backend interface (`resolve` / `read` / `write` / `list` / `exists` + index/routing ops). Unlike the process seam, storage is **required**, so the default `device-local` implementation is always present (not a no-op). Backend plugins register a named backend; selection is `storage.backend` config with the fail-loud rule above.

### The index/routing/config principle (from the boundary pass)

> **The kernel keeps the *index / routing / config mechanisms* (de-vaulted onto the storage seam). A backend keeps only its own storage specifics. Plugins own their domain fields, their detection rules, and their orchestration verbs.**

So `repo_registry`, slug resolution, `detect_project`, and `project_config` stay as kernel mechanisms that *persist through whatever backend is active*; the vault-specific machinery (`vault_probe`, `conflict-merger`) follows the vault backend out to the `obsidian-vault` plugin.

### Target layout

```
agentm — MEMORY OS + PLUGIN HOST              crickets — CAPABILITY PLUGINS (native, generated)
────────────────────────────────             ─────────────────────────────────────────────────
memory engine (storage-agnostic)             engineering-process   (memory↔process client)
  recall / reflect core                      wiki / documentation
  ── memory↔process seam (exports) ──►        project-management / meta-loop
  ── memory↔storage seam (exports) ──►        obsidian-vault        (memory↔storage backend)
  device-local backend (default)                  │ generate.py → dist/<host>/
routing/index/config (de-vaulted)                 ▼
plugin host + adapters + installer           native Claude Code + Antigravity plugins
doctor · agentm-update · telemetry           (agentm dogfoods these to develop agentm)
```

## The vault-conserving cutover

The invariant: **at every commit on both repos, the operator's existing Obsidian-vault-backed memory + state stays the live, authoritative store — readable and writable — with zero manual migration.** The danger is the *transition*, so V5 moves the storage default as a live cutover, the expand→parallel-run→contract pattern (the same copy-then-delete-after-proof discipline the V3 generator used):

| Step | Repo | Change | Vault during the step |
|---|---|---|---|
| **Expand** | agentm | Introduce the storage seam; refactor the existing vault logic to sit *behind* it as a built-in backend; add `device-local` as the fresh-install default. Operator config keeps selecting the vault, **seeded from the existing `vault_path`** — no re-setup. | Unchanged, reached through the seam. |
| **Parallel-run** | crickets | Ship the `obsidian-vault` plugin providing the same backend; install it. Both backends point at the same vault; contract tests assert identical resolution; dogfood on the real vault. | Live, doubly-served, provably identical. |
| **Contract** | agentm | Once the plugin is proven, delete the built-in vault backend. Kernel ships only `device-local`; vault-backing lives solely in the installed plugin. | Carried by the already-proven plugin; fail-loud guard prevents silent demotion. |

The consequence: **`obsidian-vault` is the first capability plugin chronologically** — the foundation the rest stands on, not a "later" item.

## Wave sequencing

Waves are crickets minor releases (milestones). V5 ships **granularly across many 3.x releases**, in dependency order:

1. agentm — **storage seam** (expand; device-local default; vault behind the seam)
2. crickets — **`obsidian-vault` plugin** (parallel-run; adopts the existing vault config)
3. agentm — **`memory ↔ process` seam** + **contract** (delete the built-in vault backend, after #2 is proven)
4. crickets — **`engineering-process` plugin** (its state rides on the `obsidian-vault` backing)
5. crickets — **wiki plugin**, then **PM plugin**, then the broader opinionated catalog

agentm carries a parallel **kernel-repositioning track** on its roadmap (the two seams, device-local default, the `auto_orchestration` split, the memory-OS narrative). Cross-repo CI green on both sides before any cutover step closes; agentm-first within each step.

## Alternatives considered

- **agentm becomes a plugin itself.** Rejected — agentm *is* the OS and the plugin host; the install/adapter substrate is how it hosts, not a tenant to extract.
- **Flag-day storage switch.** Rejected — flipping the default to device-local in one step orphans the operator's live vault. The live cutover (expand→parallel-run→contract) is the only safe path.
- **Keep vault-canonical hardwired in the kernel.** Rejected — that's what blocks storage-agnosticism and a clean reusable memory engine. Vault-canonical is conserved *for the operator* via the backing plugin, not in the kernel.
- **One megadesign for the whole catalog.** Rejected — granular waves stay independently shippable and dogfood-provable, which is the property that keeps the vault safe at every step.

## Load-bearing assumptions + re-audit triggers

- **The native-plugin model carries capabilities** (crickets v3 generator). *Re-audit if a host changes its plugin contract.*
- **Antigravity hooks are observe/side-effect-only** — kill-switch/steer are Claude-only-effective; commit-on-stop works on both. *Re-audit if Antigravity ships hook-veto.*
- **The memory→process dependency arrow stays one-way** (memory never imports the process). *Re-audit if any kernel module imports a capability plugin.*
- **The storage seam is sufficient** for both device-local and vault semantics (incl. sync-conflict handling living plugin-side). *Re-audit if a backend needs an operation the seam doesn't expose.*
- **crickets-as-dev-dependency is acceptable** — agentm's development requires crickets installed. *Re-audit if agentm ever needs to be developed in an environment that cannot install crickets.*
- **Shared memory-layer infra (the auto-context resolver `harness_memory.py`, `repo_registry.py`, run-mode config `agentm_config.py`) stays kernel-resident — there is no standalone "memory plugin."** The `wiki-maintenance` design (2026-06-04) leaned on these as a graceful-skip dependency and floated a future "memory plugin" as their owner; V5 keeps the memory *engine as the kernel*, so that framing likely doesn't track. *Re-audit at V5 planning: confirm whether this resolver/registry/config seam stays kernel-resident or splits to the storage plugin — but do not assume a standalone memory plugin. (Operator-flagged 2026-06-04: "not sure that tracks anymore — revisit then, not now.")*

## Lifecycle

Per the operator's [[hld-evolution-update-on-major-release]] convention, this HLD gets a new dated subsection whenever a release introduces, changes, or locks a relevant design call.

**Update history:**

- **v0.1 — 2026-06-03** — initial publication. The V5 design pass: locks agentm-as-memory-OS-+-plugin-host, the storage-agnostic core with device-local default, the two seams, the capability-unbundling boundary, and the vault-conserving cutover. The detailed working design (full file-by-file classification, part split, quality attributes) lives in the confidential `crickets-v3x-bundle-catalog` design; this HLD is the public architecture of record.

## See also

- [`agent-memory-evolution.md`](agent-memory-evolution.md) — the V1→V7 evolution of Agent Memory (V5 = this shift; V6 = indexed retrieval; V7 = dreaming)
- [`device-wide-architecture.md`](device-wide-architecture.md) — V4 device-wide architecture (whose vault-canonical principle V5 generalizes)
- [`crickets-v3-native-plugins.md`](https://github.com/alexherrero/crickets/wiki/crickets-v3-native-plugins) — the native-plugin generator V5 builds on
- [crickets ADR 0014](../decisions/0014-install-decoupling.md) — install decoupling · [crickets ADR 0016](../decisions/0016-project-surface-split.md) / agentm ADR 0008 — the Project surface split
