---
name: memory-reflect-stop
description: "Stop-event hook that keeps the session's episodic trace current: on every Stop it rewrites the session's one trace under memory/episodic/ from the transcript (episodic_trace.py). It mines nothing and files nothing — since task 199 lessons are compiled at night from every session's new slice, keyed by a per-session bookmark in the engine's state directory (lesson_compile.py). Never blocks session end."
kind: hook
supported_hosts: [claude-code]
version: 0.2.0
---

# memory-reflect-stop — keep the session's trace current on Stop

A `Stop` event hook that rewrites the just-ended turn's session trace (via `skills/memory/scripts/episodic_trace.py`). The trace is the session's handoff record: what it asked, how it came out, what it captured and recalled, the lines the miner noticed in passing (`## Candidates`) and the idea remarks it made (`## Ideas`). The night's idea judge and the dreaming binary's promote job read those sections.

Until task 199 this hook also mined the transcript with `reflect.py --route` and filed what it found. It did so once per session, at the first Stop, and every later turn of a long session went unread. Mining moved to the night: the lesson compile reads every session's new slice since its bookmark and writes at most five lessons per project per day. See [`lesson_compile.py`](../../skills/memory/scripts/lesson_compile.py).

## How it works

- **Trigger:** Claude Code's `Stop` event (matcher `.*` — fires at the end of every agent turn).
- **Transcript resolution:** parses the Stop event's stdin JSON payload and reads **`transcript_path`**, which Claude Code sends on every hook event. That value is used verbatim. Claude Code's hook docs direct hooks to use it rather than constructing a path from `session_id`, because the session-id → transcript-filename correspondence is not guaranteed — resume, compact, fork and `/branch` all mint session ids whose transcript may live elsewhere or not exist yet under the computed name.
  - **Fallback:** when the payload carries no `transcript_path` (a Claude Code build predating the field), the path is computed as `~/.claude/projects/<cwd-slug>/<session_id>.jsonl`, where `<cwd-slug>` is `cwd` with both `/` and `.` replaced by `-` and **no** extra leading `-` (an absolute path's own leading `/` supplies it). Pinned against known-good literals by [`scripts/test_transcript_slug.py`](../../../scripts/test_transcript_slug.py).
  - A `transcript_path` that names a file which doesn't exist is reported and skipped — never quietly retried against the computed path.
- **The trace:** `episodic_trace.py <transcript> --session <sid> --surface claude-code`, on every Stop. The trace is a merge into the session's one note, found by its `session:` id, so rewriting it is safe and every turn reaches it.
- **Output:** nothing on stdout; a "transcript not found" or "no session_id" note on stderr when it skips.
- **Exit 0 always.**

## What it never does

- **Never blocks session end.** If anything fails, the hook exits 0.
- **Never mines or files a card.** The regex miner's candidates still reach the trace's `## Candidates`, through the trace writer; they reach the vault only through the night's compile.
- **Never writes a `.harness` marker.** The `.start` / `.reflected` markers retired with task 199; the idle hook clears any left behind.
- **Never modifies the transcript.**

## Failure modes (all soft)

- **No vault resolves** — the trace has nowhere to go; exit 0.
- **Stdin payload missing `session_id`** — "no session_id on stdin" + exit 0.
- **Transcript path doesn't exist** — "transcript not found: <path>" + exit 0.
- **episodic_trace.py not installed, or python3 missing** — exit 0 silently.

## Antigravity equivalent

Not registered on `agy`. The trace writer reads Claude Code's transcript format. An agy session reaches the vault two ways: a capture through the `memory_capture` tool the installer registers with `agy mcp`, and the night's lesson compile, which reads agy's `transcript_full.jsonl` with its own bookmarks and has Flash digest each new slice. Until `lesson_compile.agy_enabled` is true, those digests go to a dry report in the engine's state directory and never reach the vault.

## See also

- [`lesson_compile.py`](../../skills/memory/scripts/lesson_compile.py) — the night's per-project lesson compile, which replaced mining here.
- [`episodic_trace.py`](../../skills/memory/scripts/episodic_trace.py) — the trace writer this hook calls.
- [`memory-reflect-idle`](../memory-reflect-idle/hook.md) — clears the retired session markers and fires the idle chain.
- [`memory` skill — `/memory reflect`](../../skills/memory/SKILL.md) — the operator's own run of the regex miner, still available by hand.
- [MemoryVault reflection-and-recovery part](../../wiki/explanation/designs/memoryvault/parts/reflection-and-recovery.md) — full architectural context.
