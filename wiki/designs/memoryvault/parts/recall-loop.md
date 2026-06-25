---
parent: memoryvault.md
part_slug: recall-loop
title: "Recall hooks + recall engine"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: [write-primitives]
estimated_scope: L
prd:
project:
---

# Recall hooks + recall engine

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §3 (Recall hooks — SessionStart + UserPromptSubmit) and §4 (Recall engine — sqlite-vec + grep+frontmatter merge) for full architectural context.

## Scope

This part ships the **read loop** — the two hooks that inject MemoryVault content into the agent's context + the recall engine they call. Together these make the vault actually usable: without recall, save is theater.

**Two-hook recall pattern**:

- **SessionStart hook** — fires once on session boot. Globs `MemoryVault/personal-private/_always-load/*.md`, reads each, formats as a single markdown block, injects into session context. Outputs a "Loaded N MemoryVault always-load entries" transparency line. Hard time budget 500ms; degraded-graceful on overrun.
- **UserPromptSubmit hook** — fires on every user message. Takes the prompt as the recall query, calls the recall engine for top-K (K=5 default) relevant entries, dedups against the already-loaded `_always-load` entries (by file path), and injects the remaining matches as a system message before the agent processes the prompt. Outputs a "Loaded N relevant entries: <slug-list>" transparency line so the user can see what memory shaped the response. Hard time budget 300ms; degraded-graceful on overrun.

**Recall engine** (called by both hooks; also exposable as `/memory search` for manual semantic queries):

1. **Embed the query** — local `sentence-transformers` (BGE-large default; ~1.3GB checkpoint cached at `~/.cache/crickets/sentence-transformers/`; PyTorch MPS on Apple Silicon for acceleration) as of v0.9.2 (see [ADR 0001's 2026-05-20 amendment](../../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). Synchronous call; on time-budget overrun, fall back to grep-only.
2. **Vec search** — query sqlite-vec for top-K nearest entries by cosine similarity. Returns `(path, similarity_score)`.
3. **Grep + frontmatter search** in parallel — scan entry titles + tags + first-line content for keyword matches; filter by frontmatter (`status: active` only by default; respect `group` filter if query specifies; respect `--include-inbox` flag for `_inbox/` access).
4. **Merge** — union the two result sets; rank by `similarity_score × 0.7 + keyword_match_count × 0.3`; dedup.
5. **Return top-K** — default K=5.

Filtering invariants: recall never surfaces `status: superseded` entries by default; `_inbox/` excluded unless `--include-inbox` flag set; `_archive/` always excluded.

**Local-only as of v0.9.2** ([ADR 0001 amendment](../../decisions/0001-crickets-purpose.md#amendment-2026-05-20)) — no API path remains. `sentence-transformers` + BGE-large is the production mode; `AGENT_TOOLKIT_EMBEDDING_MODEL` env var swaps in a smaller model for low-spec hosts. If sentence-transformers is unavailable (not installed, or PyTorch MPS issue), recall degrades to grep+frontmatter-only.

## Dependencies

- **`write-primitives`** — recall reads entries written by `/memory save`. Without entries, recall returns empty; with entries, recall validates that save's frontmatter + vec-indexing actually work end-to-end. This dependency is the natural sequencing gate: save must ship first.

## Verification criteria

1. **SessionStart hook fires on session start** — install the hook at the 2 host destinations; start a Claude Code session against a scratch vault with seeded `_always-load/`; verify the hook runs + injects entries + outputs the transparency line.
2. **UserPromptSubmit hook fires on every user message** — same scratch session; submit multiple prompts; verify the hook fires per prompt + recall returns relevant matches + dedups against already-loaded entries.
3. **Recall returns top-K relevant entries** — seed a fixture vault with ~20 entries on varied topics; submit queries with both keyword-match and paraphrase semantics; verify top-K returns the expected matches per the rubric (semantic queries should pull paraphrase matches that grep alone would miss).
4. **Time budgets respected** — SessionStart completes in <500ms; UserPromptSubmit completes in <300ms; on overrun, hook logs warning + proceeds with partial results rather than blocking.
5. **`status: superseded` filtered by default** — create an entry, evolve it; verify recall returns only the new entry (not the superseded one).
6. **Offline-capable recall works** — verify recall succeeds with network disconnected once BGE-large model is cached locally (v0.9.2 is local-only by default — see [ADR 0001 amendment](../../decisions/0001-crickets-purpose.md#amendment-2026-05-20); no API path exists to fall back to).
7. **Local-embedding failure → grep fallback** — simulate sentence-transformers missing; verify recall degrades to grep+frontmatter-only rather than failing entirely.
8. **Smoke install verifies hooks land** — `smoke-install-bash.sh` + `.ps1` extended to verify both hook scripts install at the 2 host destinations.
9. **All 3 OS CI workflows green** on the commit that lands this part.

## Notes for the implementing /work session

- The recall engine is shared between SessionStart, UserPromptSubmit, and (eventually) `/memory search`. Build it as a single Python module (`memory/recall_engine.py` or similar) callable from each hook script + the skill's search sub-command.
- The 300ms UserPromptSubmit budget is tight. Consider an embedding cache: tokenize the prompt → check a small LRU cache of recent embeddings → cache hit avoids the API/local call entirely. Defer caching to a follow-up if v1 hits the budget without it.
- The merge formula (`sim × 0.7 + keyword × 0.3`) is a guess per Tech Debt #7. Ship instrumented — every recall logs its components so the user can validate via `/memory inspect` and tune from real use.
- `_always-load/` is just a path convention; the SessionStart hook doesn't filter by frontmatter `always_load: true` — it just loads everything in that subdir. The `always_load: true` frontmatter is informational only in v1 (might be used by `/memory inspect` to surface always-load entries differently).
