# ADR 0010 — Vault internal taxonomy: what belongs in each top-level area

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-05

## Context

The AgentMemory vault has four top-level areas — `personal-private/`, `projects/`, `_idea-incubator/`, `_meta/` — but **no written rule** said what belongs in each. The harness code encoded the boundary implicitly (`_ALWAYS_LOAD_REL = ("personal-private", "_always-load")`; `_vault_projects_dir()` resolves the top-level `projects/`), but nothing documented the *semantics* a new reader or writer should follow.

The gap bit during crickets wiki-maintenance part 3 (the style-learning loop). Its task-1 resolver and task-3 writer placed project-keyed wiki-style stores at `<vault>/personal-private/projects/<slug>/wiki-style/` and `…/personal-private/projects/_global/wiki-style/` — nesting a `projects/` tree **under** `personal-private/`. That path does not exist in the live vault (real projects live at the top-level `projects/`), so the resolver silently read an empty overlay. Unit tests missed it because they built tmp vaults matching whatever path the code used (code tested against itself). The divergence only surfaced in part-3 task 4, when the `_always-load → _global` relocation required **two** readers — the crickets resolver and agentm's `documenter-context` resolver — to agree on one real path. They could not, because one read `personal-private/projects/…` and the other read top-level `projects/…`.

The likely cause: the author saw `_always-load/docs-prose-style.md` (a personal voice convention) under `personal-private/` and over-generalized — *"wiki-style is operator conventions, so the whole tree goes under `personal-private/` too"* — conflating the always-load **personal** base with **project-keyed** conventions.

**Open questions this decision resolves:**

- What is the semantic of each top-level vault area — who writes there, and what kind of data?
- Where do project-keyed stores live: top-level `projects/`, or nested under `personal-private/`?
- Where does the *global* wiki voice live — it feels personal, but the design wants it on-demand?
- What rule prevents the next dataset (not just wiki-style) from drifting the same way?

## Decision

### 1. Four areas, four semantics

| Area | Holds | Project-keyed? | Auto-injected? |
|---|---|---|---|
| `personal-private/` | the operator's personal, cross-cutting data — preferences, domains, inbox, and the `_always-load/` subset | **No** | only `_always-load/` |
| `projects/<slug>/` | per-project work memory — `_harness/`, `decisions/`, `conventions/`, `wiki-style/`, `_index.md` | **Yes** | No |
| `projects/_global/` | reserved cross-project **pseudo-project** for on-demand globals (the relocation target for global wiki voice) | yes (pseudo) | No |
| `_idea-incubator/` | pre-project ideas and research, before a project exists | No | No |
| `_meta/` | vault infrastructure — embeddings, indexes, backups, manifests | No | No |

### 2. The governing rule

**Anything project-keyed — including the `_global` pseudo-project — lives under the top-level `projects/` root. `personal-private/` is for personal, cross-cutting, non-project-keyed data; its `_always-load/` subset is the always-injected globals. Never nest `projects/` under `personal-private/`.**

Project-keyed data and personal data are distinct axes. Resolvers scope per-project reads through one root (`_vault_projects_dir()`); the `_global` pseudo-project reuses that exact read path, which is why it belongs alongside the real projects, not under the personal tree.

**Why not nest project-keyed stores under `personal-private/`** (the drift this corrects): it puts two unrelated axes — *is this private?* and *is this project-scoped?* — into one path segment. It also forks the projects root in two (`projects/` for harness state, `personal-private/projects/` for wiki-style), so a reader scanning a project's memory misses half of it. The live vault, agentm's `_vault_projects_dir()`, and the crickets wiki-maintenance design all already use top-level `projects/` — the nested path was the outlier.

### 3. The global wiki voice goes to `projects/_global/`, not `_always-load/`

The cross-project "house voice" feels personal, so `_always-load/` is tempting. It is the wrong home: the entire point of the style-learning loop is to take global wiki conventions **out** of `_always-load/` (which injects them into *every* session's context, doc-related or not) and make them **on-demand**. `projects/_global/wiki-style/` achieves both properties at once — global reach *and* on-demand loading through the per-project read path — while `_always-load/` would re-impose the very context tax the design removes.

**Why not `_always-load/`:** it auto-loads into every session. A global voice that only matters at authoring time should not weigh down planning, review, or bugfix sessions. (The base-voice *source*, `_always-load/docs-prose-style.md`, correctly stays — it is the committed-floor seed distilled into crickets' base style-guide, a different artifact from the on-demand overlay.)

## Consequences

**Positive**

- One canonical, written rule. The next dataset (not just wiki-style) has an unambiguous home, and the implicit code encoding now has an explicit spec behind it.
- The two `_global` readers (crickets `style_resolver.py`, agentm `documenter-context`) align on one real path — the part-3 task-4 relocation can verify end-to-end.
- An always-load pointer (`vault-internal-taxonomy`) makes future sessions honor the rule without re-deriving it.

**Negative**

- A small one-time correction: crickets' part-1/3 path constants drop the stray `personal-private/` segment (code-only — no live-vault data existed to migrate).
- The `_global`-feels-personal tension is real; the rule resolves it by precedence (on-demand wins over personal-feel), which a future reader may need re-justified — hence this ADR.

**Load-bearing assumptions (with re-audit triggers)**

- The vault root names are stable. **Re-audit trigger:** the planned rename of `personal-private/` → `personal/` — when it lands, update this ADR, `_ALWAYS_LOAD_REL`, the `vault-internal-taxonomy` always-load entry, and any literal references. (This ADR uses the current `personal-private` name throughout.)
- Project reads go through `_vault_projects_dir()` (top-level `projects/`, legacy `personal-projects/` fallback). **Re-audit trigger:** any new vault-rename migration (cf. V4 #26).
- The global tier is file-read. **Re-audit trigger:** agentm V6 indexed-recall — when the global tier becomes vector-discovered, "lives under `projects/_global/`" becomes a provenance tag rather than a read path.

## Amendment — 2026-06-18 (V5-3)

**Re-audit trigger #1 fired: `personal-private/` renamed to `personal/`.**

The first load-bearing assumption in this ADR stated: *"The vault root names are stable. Re-audit trigger: the planned rename of `personal-private/` → `personal/` — when it lands, update this ADR, `_ALWAYS_LOAD_REL`, the `vault-internal-taxonomy` always-load entry, and any literal references."*

That rename shipped in agentm v5.3.0 (commit `053217b`) as part of the V5-3 storage cutover — in lockstep across three layers: the vault directory itself, the `_ALWAYS_LOAD_REL` constant in `harness_memory.py`, and all functional path references in the kernel and scripts tree. **All literal references to `personal-private` in this ADR now read `personal`.** The governing rule in Decision §2 and the table in §1 remain correct with the new name.

**`resolve_documenter_context()` now always returns `None` (V5-3 storage cutover).** The Related entry below that described `resolve_documenter_context()` as reading `projects/_global/wiki-style/` is superseded: after V5-3 that function always returns `None`, and documenter context is provided by the V5-9 MCP memory server. The taxonomy itself (`projects/` is the project-keyed root, `personal/` is cross-cutting personal data) is unchanged.

## Related

- [ADR 0003 — ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking.md) — the `projects/` ownership model this taxonomy extends.
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config.md) — the sibling "config never lives in the vault; the vault is where data sits" principle.
- [ADR 0018 — V5-3 storage cutover](0018-v5-3-storage-cutover.md) — the release that fired this ADR's first re-audit trigger and shipped the `personal/` rename.
- crickets wiki-maintenance design (style-learning loop) — the consumer whose part-3 task-4 relocation surfaced the gap and aligns on `projects/_global/wiki-style/`.
- `resolve_documenter_context()` in `scripts/harness_memory.py` — **V5-3 note:** after the storage cutover this function always returns `None`; context is provided by the V5-9 MCP server. The taxonomy (projects-keyed data under `projects/`) is unchanged.
