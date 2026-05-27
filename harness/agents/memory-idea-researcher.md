---
name: memory-idea-researcher
description: Read-only deep-research worker for MemoryVault idea-incubator entries. Reads an existing `_idea-incubator/<slug>/_index.md` skeleton + scans existing MemoryVault entries + scans existing Obsidian notes + does bounded web research; fills `research-<source>.md` / `related-memoryvault.md` / `related-obsidian.md` placeholder files. Caller dispatches with the slug as the rubric; sub-agent enforces wall-time / web-fetch / token budget caps from the skeleton's frontmatter. Plan #7a part 4 task 3.
kind: agent
supported_hosts: [claude-code, antigravity]
version: 0.1.0
install_scope: either
---

# memory-idea-researcher — deep-research worker for idea-incubator entries

A read-only sub-agent that takes a freshly-created `_idea-incubator/<slug>/` skeleton (built by `ideas_incubator.py`, plan #7a part 4 task 3) and fills its placeholder files via:

1. **Cross-reference scan** against existing MemoryVault entries — uses the recall engine to surface entries related to the idea's title + summary; writes findings to `related-memoryvault.md`.
2. **Obsidian-notes scan** against the user's Obsidian vault (excluding the `MemoryVault/` subtree) — keyword + filename matches; writes findings to `related-obsidian.md`. **Read-only** — never modifies Obsidian notes.
3. **Web research** — up to N web fetches (default 3) on queries derived from the idea's keywords + surface summary; writes each fetch result to `research-<source-slug>.md`; replaces the initial `research-pending.md` placeholder.

After all three passes (or on budget overrun), the sub-agent updates `_index.md`'s frontmatter:

- `research_status: complete` — all three passes finished within budget.
- `research_status: partial` — one or more passes exceeded budget; sub-agent emits a warning + the partial-result files stand.

The sub-agent never modifies `_index.md`'s **body** (only frontmatter). The body is the operator's curated reasoning surface; the sub-agent's contribution is the research files alongside.

## Caller-supplies-inline-rubric contract

The caller dispatches with the skeleton's slug + (optionally) overrides for the budget caps. The dispatch prompt should include:

```
Use the memory-idea-researcher sub-agent to fill the deep-research files for
_idea-incubator/<slug>/. Read the skeleton's _index.md for context. Honor the
budget caps listed in the frontmatter:
  - research_budget_wall_time_sec
  - research_budget_web_fetches
  - research_budget_tokens
```

The sub-agent's allowlist (below) makes this rubric pattern enforceable — it can only do what the dispatched task explicitly asks. No write to MemoryVault entries, no modifications to Obsidian notes, no shell access beyond what `WebFetch` covers.

## Tool allowlist

**`Read, Glob, Grep, WebFetch`** — read-only file operations + bounded network access. No Bash, no Write/Edit on entries it didn't create. The sub-agent CAN write only to:

- `MemoryVault/personal-private/_idea-incubator/<slug>/research-<source>.md` (new files; one per fetch)
- `MemoryVault/personal-private/_idea-incubator/<slug>/related-memoryvault.md` (replaces placeholder)
- `MemoryVault/personal-private/_idea-incubator/<slug>/related-obsidian.md` (replaces placeholder)
- `MemoryVault/personal-private/_idea-incubator/<slug>/_index.md` (frontmatter ONLY — the `research_status:` field)

Writes outside this allowlist are bugs in the sub-agent's dispatch + should be caught at PR review time.

## Budget caps (locked design call B1.i)

Defaults from `ideas_incubator.py`:

| Cap | Default | Frontmatter key |
|---|---|---|
| Wall-time | 300s (5 min) | `research_budget_wall_time_sec` |
| Web fetches | 3 | `research_budget_web_fetches` |
| Tokens (input+output) | 5000 | `research_budget_tokens` |

Budget overrun produces **partial results + a flag**, never blocks the calling session. The sub-agent should:

1. Pass 1 (cross-ref MemoryVault) first — fastest, no network, often satisfies the operator's "did we already think about this?" question.
2. Pass 2 (Obsidian scan) — local filesystem, fast.
3. Pass 3 (web research) last — bounded by N fetches; the sub-agent emits a final summary tying the fetches together.

If wall-time depletes mid-pass-3, the sub-agent emits whatever fetches completed + sets `research_status: partial` in `_index.md` frontmatter.

## What it never does

- **Never modifies entries outside `_idea-incubator/<slug>/`.** No save to `personal-private/preferences/`, no edit to `projects/` (or its legacy alias `personal-projects/`).
- **Never writes to `~/Obsidian/` notes** (only reads them for the scan).
- **Never invokes `/memory save` or `/memory evolve`.** Promotion is the operator's job via `/memory promote idea <slug>` (plan #7a part 4 task 4).
- **Never exceeds the budget caps.** On overrun: emit partial + flag.
- **Never modifies `_index.md` body content.** Only frontmatter `research_status:` field gets toggled.

## Failure modes (all soft)

- **Skeleton not found** — return error to caller; no files written.
- **Budget overrun mid-pass** — emit partial + set `research_status: partial`; return success with overrun warning.
- **WebFetch fails on a specific URL** — record failure in the corresponding `research-*.md` + continue with remaining fetches.
- **Obsidian vault doesn't exist** (operator doesn't use Obsidian) — `related-obsidian.md` reads "no Obsidian vault detected at ~/Obsidian/; skipped".

## See also

- [`ideas_incubator.py`](../skills/memory/scripts/ideas_incubator.py) — Python skeleton creator that this sub-agent fills.
- [`ideas_surface.py`](../skills/memory/scripts/ideas_surface.py) — Tier-1 surface writer; complements this Tier-2 worker.
- [`recall.py`](../skills/memory/scripts/recall.py) — recall engine the sub-agent invokes for the cross-reference scan.
- [evaluator sub-agent](evaluator.md) — reference shape for the caller-supplies-inline-rubric pattern. Same fresh-context framing applies: the researcher reads the skeleton's `_index.md` + the rubric the caller dispatched with, nothing more.
- [MemoryVault idea-ledger part](../wiki/explanation/designs/memoryvault/parts/idea-ledger.md) — full architectural context including the two-tier capture system + promotion + GC.
