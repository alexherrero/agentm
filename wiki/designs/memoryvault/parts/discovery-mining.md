---
parent: memoryvault.md
part_slug: discovery-mining
title: "Discovery + mining — transcript reflection + personal-skills indexer + internet skill-scan"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: [write-primitives, recall-loop, reflection-and-recovery, idea-ledger, seed-pass]
estimated_scope: L
plan: "7b"
prd:
project:
---

# Discovery + mining — transcript reflection + personal-skills indexer + internet skill-scan

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §8 (Discovery + mining) for full architectural context. **This is the single part for plan #7b** — ships after #7a has been dogfooded for 1-2 weeks of real use, so designs can absorb lessons from #7a before adding the discovery layer.

## Scope

Three sub-components, all in plan #7b:

**1. Transcript reflection pass** (one-time + ongoing):

- **One-time pass**: run the reflection-and-recovery logic against the historical Claude Code transcripts at `~/.claude/projects/*/`. Mines months of past sessions for the 3 extraction categories + idea candidates, retroactively populating MemoryVault from work that pre-dates the skill.
- **Ongoing pass**: the existing Stop + idle hooks (shipped in `reflection-and-recovery` part) already handle each new session. This sub-component just extends them to optionally scan transcripts from sessions that ran on other machines or other Claude Code installations the user might have.
- **Seed manifest awareness**: this pass reads `MemoryVault/_meta/seed-manifest-20260515.md` (written by the `seed-pass` part) and skips re-capturing content that was already seeded — avoids duplication.

**2. Personal-skills auto-indexer**:

- Walks every `SKILL.md` in `crickets/skills/` + `agentm/.claude/skills/` (plus any other sibling-installed repos with skill directories).
- Writes one MemoryVault entry per SKILL.md to `MemoryVault/personal-skills/<repo>/<skill-name>.md` with frontmatter (`kind: skill-pointer`, `source_path`, `source_repo`, `last_indexed`, `skill_version`).
- Entry body = skill manifest summary + the skill's `description` + key sub-commands + tool allowlist.
- Runs at toolkit install time + on `/release` events + on-demand via `/memory index-skills`.
- Pre-hook injection (from `recall-loop` part) merges entries from BOTH `personal-private/` (preferences) AND `personal-skills/` (available skills) at query time — so the agent learns "we have a `/design author` skill" without being told every session.

**3. Internet skill-discovery scan**:

- Periodic scan (cadence TBD — weekly default; configurable via `memory.skill_discovery_cadence`) of curated sources for SKILL.md-shaped patterns worth adopting.
- **Source whitelist** (TBD precise list — to settle during implementation): GitHub trending with `claude-code` / `agent-skills` / `claude-skills` tags + named awesome-lists (curated by the user during implementation) + Anthropic Cookbook + named blog feeds. The source whitelist is itself a MemoryVault entry at `MemoryVault/personal-private/skill-discovery-sources.md` so the user can edit it without code changes.
- **Adapt-don't-import principle**: when a relevant pattern is found, the agent does NOT fork the SKILL.md into `crickets/skills/`. Instead:
  1. The agent surfaces the pattern as an idea candidate (similar shape to the idea-ledger flow).
  2. Writes a `personal-skill-watchlist` MemoryVault entry capturing what's interesting about the pattern + what would need adapting for personal use + a link to the source.
  3. The user (not the agent) reviews the entry + decides whether to author an actual skill in `crickets/` that adapts the pattern.
- **Surface location for the watchlist** (TBD — to settle during implementation): one candidate is `MemoryVault/personal-private/_skill-watchlist/<source-slug>/<pattern-slug>.md` (dedicated dir, distinct from `_idea-incubator/` because skill discoveries are a specific shape).

**Cadence + behavior**:

- Internet skill-scan runs in the background via the idle-time hook (extends the existing idle infrastructure from `reflection-and-recovery`).
- High-confidence patterns (strong match against user's existing skill preferences in MemoryVault) → surface immediately in interactive review.
- Medium/low-confidence patterns → land in `_skill-watchlist/` for batch review (`/memory watchlist` command shows the current backlog).

## Dependencies

All five #7a parts:

- **`write-primitives`** — every component writes via `/memory save` (skill-pointer entries + watchlist entries + transcript-mined entries).
- **`recall-loop`** — personal-skills entries become recall-targets at query time; the indexer needs the recall infrastructure to validate entries surface correctly.
- **`reflection-and-recovery`** — transcript-mining sub-component reuses the reflection logic; internet skill-scan extends the idle-time hook.
- **`idea-ledger`** — the adapt-don't-import workflow produces watchlist entries that look like idea-ledger entries (deep research at capture time, promotion path); reuses the idea-ledger primitives.
- **`seed-pass`** — the transcript-reflection one-time pass needs the seed manifest to know what's already captured.

#7b also depends on **#7a being dogfooded for 1-2 weeks** before this part starts — gives time for lessons to feed back into the discovery-mining design.

## Verification criteria

1. **Transcript reflection one-time pass works** — point at `~/.claude/projects/*/`; verify it processes historical sessions; verify entries land in MemoryVault per tri-modal routing (HIGH/MEDIUM/LOW); verify the pass respects the seed manifest and skips already-seeded content.
2. **Transcript reflection respects rate limits** — large transcript backlogs shouldn't blow through the embedding API; verify the pass batches + paces; verify it can be paused + resumed.
3. **Personal-skills indexer runs at install time** — install crickets fresh; verify `personal-skills/` populated with one entry per SKILL.md in toolkit + harness; verify entries have correct frontmatter.
4. **Personal-skills indexer runs on `/release`** — simulate a `/release` event; verify the indexer re-scans + updates entries for any skill that changed version.
5. **Personal-skills entries surface in recall** — submit a prompt that should match a skill capability; verify the personal-skills entry surfaces alongside personal-private entries.
6. **Internet skill-scan runs on schedule** — set cadence to "every 1 min" for testing; verify the idle hook fires the scan; verify it pulls from the source whitelist; verify it produces watchlist entries for matching patterns.
7. **Source whitelist editable via vault file** — modify `MemoryVault/personal-private/skill-discovery-sources.md`; verify the next scan respects the change (no code change needed).
8. **Adapt-don't-import workflow** — fixture a discovered pattern; verify the agent writes a `_skill-watchlist/` entry (NOT a fork into `crickets/skills/`); verify the entry captures what to adapt + source link.
9. **Watchlist review command** — `/memory watchlist` outputs the current backlog; user can promote / dismiss / defer entries.
10. **All 3 OS CI workflows green** on the commit that lands this part.

## Notes for the implementing /work session

- This is plan #7b's single part. It's large in scope (3 sub-components) but each sub-component is bounded. Consider whether to ship as one PR or three smaller PRs — the user's call when starting the implementing session.
- The personal-skills indexer is the simplest sub-component (file walks + writes); ship it first to validate the indexer pattern, then layer transcript-mining and internet-scan on top.
- The internet skill-scan source whitelist is **load-bearing**: without it, the watchlist floods. Start narrow (Anthropic Cookbook + 1-2 specific awesome-lists) and expand based on actual signal quality.
- The adapt-don't-import principle is the architectural commitment that protects against agent-driven skill bloat in `crickets/`. The agent should NEVER author `crickets/skills/<x>/SKILL.md` files autonomously — only the user does that, after reviewing a watchlist entry. Bake this hard rule into the implementing skill body.
- Transcript-mining for historical sessions will likely produce thousands of candidates. The tri-modal routing means most will land in `_inbox/` for batch review — plan for inbox-management UX (`/memory inbox --bulk-review` or similar) before kicking off the historical pass.
- Plan #7b ships after #7a is dogfooded — so by the time this part is implemented, the user has 1-2 weeks of real recall-quality feedback. Use that data to tune any defaults in this part (cadence, confidence thresholds, source whitelist).
