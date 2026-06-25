---
parent: memoryvault.md
part_slug: write-primitives
title: "Write primitives — `memory save` + `memory evolve`"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: []
estimated_scope: M
prd:
project:
---

# Write primitives — `memory save` + `memory evolve`

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §1 for the full architectural context.

## Scope

This part ships the two foundational write primitives that every other part of MemoryVault depends on:

**`/memory save <kind> <slug> [--group <group>] [--always-load]`** — writes an entry at `MemoryVault/<group>/<kind>/<slug>.md` with YAML frontmatter (`kind`, `status: active`, `created`, `updated`, `tags`, optional `supersedes`). Body is free-form markdown. On save: file write is synchronous (<50ms target); embedding + vec-index update are async (don't block the agent or the user). The `--always-load` flag writes to `MemoryVault/personal-private/_always-load/` and sets `always_load: true` frontmatter — entries flagged this way get injected at SessionStart per the recall-loop part.

**`/memory evolve <old-path> <new-content> <reason>`** — atomic archive-and-replace. Five steps: (1) Read the old entry; (2) write new entry with `supersedes: <old-path>` frontmatter pointing back; (3) atomically move old entry to `MemoryVault/personal-private/_archive/<original-path>.YYYYMMDD.md` via filesystem rename (single-syscall atomicity on macOS APFS); (4) update old entry's frontmatter `status: superseded` + `superseded_by: <new-path>` cross-link; (5) trigger vec-index update for both files. Recall filters skip `status: superseded` by default.

Tri-modal confidence routing (HIGH → auto-save / MEDIUM → interactive review / LOW → `_inbox/`) applies only when the reflection sidecar invokes these primitives — direct user invocation always writes immediately to the requested location.

The skill home is `crickets/skills/memory/SKILL.md` with full YAML frontmatter (`name: memory`, `kind: skill`, `supported_hosts: [claude-code, antigravity]`, `version: 0.1.0`, `install_scope: project`) and tool allowlist `[Read, Write, Edit, Glob, Grep]` — no Bash. This part ships the skill scaffold + the `save` and `evolve` sub-command bodies only; recall/reflection/idea-ledger sub-commands stub for future parts. (Standalone Gemini CLI host removed from supported_hosts in v0.9.0 per [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) / [ADR 0006](../../decisions/0006-gemini-cli-host-removal).)

## Dependencies

None — foundational. All other parts depend on this one. The only pre-existing dependencies are:

- **sqlite-vec** Python package (`pip install sqlite-vec`) — installed lazily on first save invocation; if missing, save still succeeds (file write is unconditional) but index update is deferred until the dep lands.
- **Local `sentence-transformers`** — the only embedding mode as of v0.9.2 (see [ADR 0001's 2026-05-20 amendment](../../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). Call is async (queued); first invocation downloads BGE-large (~1.3GB) lazily; subsequent calls are on-device + offline-capable. The save side is unconditional — file write never blocks on embedding state. Embedding implementation lives in the recall-loop part.

## Verification criteria

1. **Smoke install green** — `bash crickets/scripts/smoke-install-bash.sh` (+ `.ps1`) verifies the `memory` skill installs at the 2 host destinations (`.claude/skills/`, `.agent/skills/`); negative-existence assertions verify `.agents/skills/` is NOT created (gemini-cli removed in v0.9.0).
2. **`validate-manifests.py` clean** — skill manifest parses with no errors; skill count increments.
3. **`/memory save` end-to-end** — invoke against a scratch vault; file lands at the expected path with correct frontmatter (`kind`, `status: active`, `created`, `updated`, `tags`, `group`); content matches the input.
4. **`--always-load` flag** — files written with the flag land in `_always-load/` subdir with `always_load: true` frontmatter.
5. **`/memory evolve` end-to-end** — invoke against an existing entry; verify old entry is archived to `_archive/<original-path>.YYYYMMDD.md` with `status: superseded` + `superseded_by` cross-link; new entry has `supersedes` cross-link; both files exist on disk after the operation.
6. **PII guardrails apply** — `crickets/scripts/check-no-pii.sh` clean on toolkit-committed content (skill source + templates); pre-push hook runs clean.
7. **All 3 OS CI workflows green** on the commit that lands this part.

## Notes for the implementing /work session

- This is the foundational part — every later part assumes these primitives exist + behave as specified above. If the spec needs amendment mid-execution, append to the parent design's Document History before re-running `/design translate` on this part.
- The skill body should document both sub-commands per the established pattern from `crickets/skills/design/SKILL.md` (which is the first toolkit skill with multi-sub-command bodies — use it as the reference shape).
- The vec-index file at `MemoryVault/_meta/vec-index.db` doesn't need to exist when save is first invoked; create on first call if absent.
- The `_archive/` subdir doesn't need to exist for the first save; create lazily on first evolve.
