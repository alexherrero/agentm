---
name: memory-reflect-stop
description: "Stop-event hook that mines the just-ended session's transcript for durable candidate entries and ROUTES them into the vault: every candidate to its class directory — HIGH at high filing confidence, MEDIUM/LOW/ideas flagged `filing_confidence: low`, the metadata inbox of filing v2 (tri-modal routing via reflect.py --route, default route-mode 'auto'). Emits a transparency line listing candidate counts + what got saved / filed flagged. On success renames the session's .start crash-recovery marker → .reflected. Dedup-guarded against the V4 #23 phase-dispatch (skips if the session was already reflected). Plan #7a part 3 task 3 (scaffold) + task 5 (routing) + V4 #23 (dedup)."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# memory-reflect-stop — mine the just-ended session's transcript on Stop

A `Stop` event hook that runs reflection mining (via `skills/memory/scripts/reflect.py`) against the just-ended session's transcript + emits a transparency line summarizing what got mined. The same mining logic also runs via the manual `/memory reflect` skill (plan #7a part 3 task 2) + the idle-time hook (task 4 — covers crashed sessions where this Stop hook didn't fire).

## How it works

- **Trigger:** Claude Code's `Stop` event (matcher `.*` — fires at the end of every agent turn that ends a session).
- **Transcript resolution:** parses the Stop event's stdin JSON payload and reads **`transcript_path`**, which Claude Code sends on every hook event. That value is used verbatim. Claude Code's hook docs direct hooks to use it rather than constructing a path from `session_id`, because the session-id → transcript-filename correspondence is not guaranteed — resume, compact, fork and `/branch` all mint session ids whose transcript may live elsewhere or not exist yet under the computed name.
  - **Fallback:** when the payload carries no `transcript_path` (a Claude Code build predating the field), the path is computed as `~/.claude/projects/<cwd-slug>/<session_id>.jsonl`, where `<cwd-slug>` is `cwd` with both `/` and `.` replaced by `-` and **no** extra leading `-` (an absolute path's own leading `/` supplies it). Pinned against known-good literals by [`scripts/test_transcript_slug.py`](../../../scripts/test_transcript_slug.py).
  - A `transcript_path` that names a file which doesn't exist is reported and skipped — never quietly retried against the computed path. Falling back there would be guessing at a path again, which is what reading the payload exists to retire.
- **Dedup guard (V4 #23):** before mining, if `.harness/session-id-<sid>.reflected` already exists, the session was already reflected by the post-`/work` phase-dispatch (`orchestration_phase.py`) — the hook skips (a second `reflect.py --route` would error on a HIGH-save slug collision). The two cooperate via this marker so a session is reflected exactly once.
- **Mining + routing call:** invokes `python3 .claude/skills/memory/scripts/reflect.py <transcript-path> --summary --route`. The `--route` pass files HIGH candidates at their class at high confidence and MEDIUM/LOW + ideas at their class flagged `filing_confidence: low` (nothing stages in a directory since filing v2). Route-mode defaults to `auto` (hook-safe; never prompts); `MEMORY_REVIEW_MODE=silent` auto-saves MEDIUM too.
- **Marker rename:** on a successful route, renames `.harness/session-id-<sid>.start` → `.reflected` (the crash-recovery marker the idle hook GCs after 30 days; also what the dedup guard above keys on). No-op if the `.start` marker is absent.
- **Output:**
  - **stdout** — passed through from reflect.py (one JSON record per line: the summary + route passes).
  - **stderr** — one transparency line: `[memory-reflect-stop] Mined N memory + M idea candidates from <transcript>; saved S, filed flagged for review I`.
- **Exit 0 always** — even on missing transcript, routing errors (e.g. `MEMORY_VAULT_PATH` unset), missing python3 (graceful-skip pattern across the layered failure modes).

## Implementation status

Routing **shipped** (plan #7a part 3 task 5): the hook mines AND routes — HIGH → filed at class at high confidence, MEDIUM/LOW/ideas → filed at class flagged low (tri-modal via `reflect.py --route`, default route-mode `auto`; the `_inbox/` staging directory retired with filing v2's write path). V4 #23 added the phase-dispatch **dedup guard** + the `.reflected` marker cooperation. (The original task-3 scaffold mined-but-didn't-save; that's no longer the behavior.)

## What it never does

- **Never blocks session end.** If anything fails (transcript missing, reflect.py missing, python3 missing, mining/routing error), the hook exits 0 silently.
- **Never double-reflects a session.** The `.reflected` dedup guard makes the hook and the V4 #23 phase-dispatch cooperate — whichever fires first reflects + marks; the other skips.
- **Never modifies the transcript.** Pure read-only on the transcript itself; writes only to the vault (via routing) + the `.harness/` marker.
- **Never invokes reflect.py with a non-existent transcript.** If the path doesn't resolve, hook exits 0 with a "transcript not found" stderr note.

## Failure modes (all soft)

- **`MEMORY_VAULT_PATH` unset** — `reflect.py --route` exits non-zero (no vault to route into); the hook emits a "reflect.py --route exited N (MEMORY_VAULT_PATH set?)" stderr note + exit 0, and leaves the `.start` marker intact so a later pass can retry.
- **Stdin payload missing `session_id`** — hook reports "no session_id on stdin" + exit 0.
- **Transcript path doesn't exist** — stderr "transcript not found: <path>" + exit 0.
- **reflect.py not installed** — exit 0 silently (graceful-skip; matches the recall hooks' pattern).
- **python3 not on PATH** — shell wrapper exits 0 silently.

## Antigravity equivalent

Antigravity has no first-class Stop-event surface (per v0.7.0+ installer reality). The equivalent pattern for Antigravity is a per-conversation-end skill auto-invocation; tracked under MemoryVault's discovery-mining part as a future companion customization.

## See also

- [`memory-reflect-idle`](../memory-reflect-idle/hook.md) — companion idle-time hook (lands in plan #7a part 3 task 4) that catches sessions where Stop didn't fire (crashed Claude Code).
- [`memory` skill — `/memory reflect`](../../skills/memory/SKILL.md) — manual trigger using the same mining logic.
- [`reflect.py`](../../skills/memory/scripts/reflect.py) — canonical Python implementation invoked by this hook.
- [MemoryVault reflection-and-recovery part](../../wiki/explanation/designs/memoryvault/parts/reflection-and-recovery.md) — full architectural context.
