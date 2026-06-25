---
parent: memoryvault.md
part_slug: idea-ledger
title: "Two-tier idea capture — Ideas.md + _idea-incubator/"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: [write-primitives, recall-loop, reflection-and-recovery]
estimated_scope: M
prd:
project:
---

# Two-tier idea capture — `Ideas.md` + `_idea-incubator/`

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §5 (Idea ledger) for full architectural context. This is the first real consumer of the permeable write boundary (A3 locked design call): the agent writes to `Ideas.md` outside the `MemoryVault/` folder.

## Scope

When the reflection sidecar (previous part) surfaces an **idea candidate** (not a memory-entry candidate), this part handles the two-tier write:

**Tier 1 — Surface entry (user-facing)** at `~/Obsidian/Ideas.md` (single file at the vault root, append-only). Section format:

```
## YYYY-MM-DD: <Idea Title>
<2-sentence summary of the idea>
See deep research: [[MemoryVault/personal-private/_idea-incubator/<idea-slug>/_index.md]]
```

Sections sorted by date-prefix; new ideas append to the bottom. The 2-sentence summary is curated by the agent during reflection — it's the surface-level "is this worth investigating?" pitch the user sees when they open `Ideas.md` during their normal Obsidian workflow.

**Tier 2 — Deep research entry (agent-facing)** at `MemoryVault/personal-private/_idea-incubator/<idea-slug>/`. Directory contains:

- `_index.md` — frontmatter (`kind: idea`, `status: incubating`, `surfaced_in_session: <session-id>`, `surfaced_at: <timestamp>`) + the agent's full reasoning about the idea + cross-references.
- Additional research files written by the agent at capture time: web fetch dumps (renamed to `research-<source-slug>.md`), cross-reference notes against existing MemoryVault entries (`related-memoryvault.md`), scan of existing Obsidian notes for related content (`related-obsidian.md`).

**Deep-research depth budget at capture time** (TBD precise values to settle during implementation — conservative defaults to avoid runaway): cap at 5 minutes wall-time / 3 web fetches / 5K tokens per idea. Budget enforced via timeouts in the research sub-agent.

**Promotion path** — `/memory promote idea <slug>` command graduates an idea to a real project:

1. Moves `MemoryVault/personal-private/_idea-incubator/<slug>/` → `MemoryVault/personal-projects/<slug>/`.
2. Updates the corresponding `Ideas.md` section to append `→ promoted YYYY-MM-DD to MemoryVault/personal-projects/<slug>/`.
3. Recalculates vec-index entries for the moved files (paths changed → embeddings re-keyed).

**Permeable boundary enforcement** — writes to `~/Obsidian/Ideas.md` (outside `MemoryVault/`) are first-class consumers of the A3 boundary. Per the locked design call:

- Reflection sidecar writing idea candidates → uses the **agent-initiated + user-confirmed** path: the agent proposes "I noticed N idea candidates this session: <list> — surface in `Ideas.md`?" + user confirms before the writes happen.
- User opting into silent mode (`memory.review_mode: silent`) implies confirmation by default (the user pre-confirmed); no per-idea prompt.
- Direct user invocation (`/memory idea <title> <summary>`) is **explicit user request**; writes immediately without confirmation.

**Garbage collection** — `_idea-incubator/<slug>/` entries get GC'd after N months without engagement (N default 6; configurable via `memory.incubator_gc_months`). GC presents the user with a list before deletion:

> "These ideas haven't been promoted or referenced in 6+ months: <list>. Keep / Archive / Delete?"

Never silent deletion. Archive option moves to `_idea-incubator/_archive/<slug>/` (preserves history but excludes from active recall).

## Dependencies

- **`write-primitives`** — both tiers ultimately use `/memory save` to write entries (Ideas.md is a Single-file append rather than a save-call, but the `_idea-incubator/` directory entries are written via the save path).
- **`recall-loop`** — incubator entries are recall-able (the agent can semantically search prior ideas when surfacing new ones — "did we already think about X?"); without recall, deduplication of similar ideas across sessions doesn't work.
- **`reflection-and-recovery`** — this part subscribes to the idea-candidate stream emitted by the reflection sidecar. Without the sidecar emitting candidates, this part has nothing to handle.

## Verification criteria

1. **Idea candidates produce both tiers** — fixture session with seeded idea candidates; verify each candidate produces an `Ideas.md` section + an `_idea-incubator/<slug>/` directory with `_index.md`.
2. **Deep research files written at capture** — verify `research-*.md` (web fetches), `related-memoryvault.md` (cross-refs), `related-obsidian.md` (scan results) all land in the incubator dir.
3. **Research depth budget respected** — fixture idea with large research surface; verify the wall-time cap / web-fetch cap / token cap all enforced; verify the budget overrun produces a partial result + flag rather than blocking the session.
4. **Permeable boundary enforcement** — verify writes to `~/Obsidian/Ideas.md` are gated by the agent-initiated + user-confirmed path (reflection-driven) or explicit-user-request path (`/memory idea` direct invocation); never silent.
5. **`memory.review_mode: silent`** pre-confirms idea writes — verify silent mode auto-confirms without per-idea prompts.
6. **`/memory promote idea <slug>`** command works — promote a fixture incubator entry; verify dir moves to `personal-projects/`; verify `Ideas.md` section gets the `→ promoted` annotation; verify vec-index entries recalculated.
7. **GC presents list before deletion** — seed old incubator entries (faked timestamps); run GC; verify the prompt presents the list with Keep/Archive/Delete options; verify each option does the right thing.
8. **Smoke install verifies skill paths** — `smoke-install-bash.sh` + `.ps1` verify the new sub-command bodies install correctly.
9. **All 3 OS CI workflows green** on the commit that lands this part.

## Notes for the implementing /work session

- This is the first real test of the A3 permeable write boundary. The implementation needs a clear `ask_user_to_confirm_write_outside_memoryvault(target_path, content_preview, rationale) -> bool` primitive that the reflection sidecar (and future write-outside-MemoryVault consumers) all call. Build that as a shared helper, not inline per call site.
- The deep-research sub-agent is a new pattern. Reuse the evaluator sub-agent shape from plan #3 (`crickets/agents/evaluator/`) as the template — same allowlist (Read, Glob, Grep + maybe WebFetch for the web-fetch research files), same caller-supplies-inline-rubric pattern.
- WebFetch in the research sub-agent needs careful sandboxing. The 3-fetch cap is a hard limit; the sub-agent shouldn't be able to chain fetches arbitrarily.
- Title-slug generation for ideas: the reflection sidecar emits a title from the surfaced candidate; the slug is `kebab-case(title)` truncated to 40 chars + collision-suffix (`-2`, `-3`, etc.) if a slug already exists. Defer fancy NLP for slug generation to a follow-up if simple suffix works in practice.
- `Ideas.md` should not be vec-indexed — it lives outside `MemoryVault/` and is for human reading, not agent recall. The `_idea-incubator/<slug>/` entries ARE vec-indexed (they're inside MemoryVault).
