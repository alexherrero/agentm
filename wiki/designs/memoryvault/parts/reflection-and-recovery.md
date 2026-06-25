---
parent: memoryvault.md
part_slug: reflection-and-recovery
title: "Reflection sidecar + crash-recovery markers"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: [write-primitives, recall-loop]
estimated_scope: L
prd:
project:
---

# Reflection sidecar + crash-recovery markers

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §2 (Reflection sidecar) and §6 (Crash recovery — session-id markers) for full architectural context. These two subsections are bundled because they share the Stop/idle hook scaffolding.

## Scope

This part ships the **write loop** — the reflection logic that mines conversation transcripts for durable entries + the crash-recovery mechanism that ensures no session's learnings are lost when the Stop hook doesn't fire.

**Three trigger surfaces for reflection** (all running the same logic):

- **Manual `/memory reflect [--session <path>]`** — user-initiated; runs against the current session transcript by default or a specified transcript path. Validates the reflection logic against arbitrary input.
- **Stop-event hook** — fires automatically on Claude Code session Stop. Scoped to the just-ended session's transcript.
- **Idle-time hook** (new crickets primitive added by this part) — fires when Claude Code has been idle > N minutes (N TBD, default 30); also scans `.harness/session-id-*.start` marker files for orphans (crashed sessions where Stop didn't fire) and runs reflection retroactively on those transcripts.

**Reflection logic** (shared across all three triggers):

1. **Read the session transcript** at `~/.claude/projects/<repo>/<session-id>.jsonl`.
2. **Two parallel mining passes**:
   - **3-category mine** (writes to MemoryVault): scan for Successful Workflows, User Preferences, Fixes & Workarounds. Each candidate gets a confidence rating per the heuristic in step 3.
   - **Idea-candidate mine** (writes to user-vault `Ideas.md` + deep research in `_idea-incubator/`): scan for follow-ups, future project ideas, research candidates. Each candidate gets a 2-sentence summary + the agent does deep research. (The actual idea-ledger writing is handled by the `idea-ledger` part — this part just produces the candidates.)
3. **Tri-modal routing** for 3-category candidates per the locked heuristic:
   - **HIGH** = explicit user signal during session (user said "always X" / user manually corrected the agent / user locked a design call) → auto-save via `/memory save`.
   - **MEDIUM** = pattern-inferred (agent noticed pattern 3+ times) → interactive review prompt with approve / edit / reject / skip / **supersede-existing-X** options. Controlled by `memory.review_mode: interactive (default) | silent`.
   - **LOW** = single-instance inference → write to `MemoryVault/personal-private/_inbox/<slug>.md` for batch review later.

**Crash-recovery markers**:

1. **SessionStart hook** writes `.harness/session-id-<session-uuid>.start` (one file per session, contents = session start timestamp + transcript path).
2. **Stop hook** (after reflection succeeds) renames `.start` → `.reflected`. On failure, file stays as `.start` for retry.
3. **Idle-time hook** scans for `.start` files older than 1 hour (idle threshold for assuming session is truly dead) → runs reflection retroactively → renames to `.reflected` on success.
4. **GC**: `.reflected` markers older than 30 days get deleted on next idle pass.

All markers live in `.harness/` (gitignored, runtime-only).

## Dependencies

- **`write-primitives`** — reflection writes via `/memory save` for HIGH-confidence candidates and the interactive review supersede path uses `/memory evolve`. Without write primitives, reflection has nowhere to land its output.
- **`recall-loop`** — interactive review's MEDIUM-confidence prompts surface "this candidate contradicts existing entry X — supersede?" decisions, which requires the recall engine to find the contradicting entry. Without recall, the supersede-existing-X option degenerates to plain approve/reject.

## Verification criteria

1. **Stop hook fires on session end** — install at the 2 host destinations; run a Claude Code session; verify Stop hook fires + reflection runs against the just-ended session's transcript.
2. **Idle hook fires after N min idle** — same; let session sit idle past the threshold; verify the hook fires.
3. **Reflection mines 3 categories** — fixture transcript with seeded "user said 'always X'" / "user corrected the agent" / "hit error Z resolved by W" patterns; verify each pattern surfaces as a candidate in the correct category.
4. **Reflection mines idea candidates** — fixture transcript with "we should also do X later" / "this could be its own project" patterns; verify each surfaces as an idea candidate (raw — idea-ledger part handles the write).
5. **Tri-modal routing places entries correctly** — HIGH candidates auto-save to canonical paths; MEDIUM candidates trigger interactive review prompts; LOW candidates land in `_inbox/`.
6. **Interactive review prompts work** — `memory.review_mode: interactive` default; verify each MEDIUM candidate produces an approve/edit/reject/skip/supersede prompt; verify each option does the right thing.
7. **`memory.review_mode: silent` toggle** — flip the setting; verify MEDIUM candidates auto-save without prompting.
8. **Crashed-session marker recovery** — start a session, kill Claude Code mid-session (so Stop never fires); verify `.start` marker stays in place; trigger idle hook (or wait for it); verify reflection runs retroactively against the orphan session's transcript + marker gets renamed to `.reflected`.
9. **Marker GC at 30 days** — seed old `.reflected` markers; verify idle hook deletes them on next pass.
10. **Manual `/memory reflect`** — invoke directly against a specified transcript path; verify reflection runs + produces candidates routed per tri-modal logic.
11. **Smoke install verifies hooks land** — `smoke-install-bash.sh` + `.ps1` extended for both Stop + idle hook scripts at the 2 host destinations.
12. **All 3 OS CI workflows green** on the commit that lands this part.

## Notes for the implementing /work session

- The idle-time hook is the new crickets primitive. The existing Stop-event hook pattern from plan #4+#5 (`commit-on-stop`) is the reference shape; idle is similar but with a different trigger condition. The hook needs to know "how long has the agent been idle?" — Claude Code's hook lifecycle doesn't expose this directly, so the hook likely polls a heartbeat file the SessionStart writes + updates on every UserPromptSubmit.
- The 3-category extraction heuristic is the load-bearing-but-uncertain piece. Ship instrumented — every mined candidate logs its category + confidence + rationale so the user can validate via the interactive review + manually inspect via `/memory inspect`.
- The "user said 'always X'" pattern detection is straightforward (keyword scan + nearby user-turn). The "user manually corrected the agent" pattern is harder — requires looking at agent turn → user reverts/corrects → agent applies correction. Defer fancy detection to a follow-up if the simple heuristic is too noisy.
- Idea-candidate mining lives in this part but the **idea-ledger writes** (Ideas.md + `_idea-incubator/`) are the next part's scope. This part's reflection logic just emits idea candidates as in-memory objects; the idea-ledger part subscribes to those.
- `.harness/session-id-*.start` marker contents: just the session start timestamp + transcript path is sufficient. Don't put PII (transcript content) in markers — they're runtime metadata.
