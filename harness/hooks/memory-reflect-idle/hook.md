---
name: memory-reflect-idle
description: "SessionStart hook that clears the retired .harness/session-id-*.start and .reflected markers and launches the cooldown-gated idle orchestration chain (discover-skills, adapt Pass-1). Until task 199 it swept those markers for crashed sessions and mined them; mining moved to the night's lesson compile, keyed by a per-session bookmark in the engine's state directory, and the markers retired. Never blocks session start."
kind: hook
supported_hosts: [claude-code]
version: 0.2.0
---

# memory-reflect-idle — clear the retired markers, fire the idle chain

A `SessionStart` hook with two jobs since task 199.

## Retired markers

Until task 199, SessionStart wrote `.harness/session-id-<sid>.start`, the Stop hook renamed it `.reflected` once it had mined the session, and this hook swept `.start` markers past an hour idle as crashed sessions and mined them retroactively. The result was a session mined once, at its first Stop, or at most again after a crash or compaction; every later turn of a long session went unread.

Mining moved to the night. The lesson compile ([`lesson_compile.py`](../../skills/memory/scripts/lesson_compile.py)) keeps a high-water mark per session in the engine's state directory and scans every transcript folder, worktree and chip sessions included, so it needs no marker to find a session. Nothing writes or reads a marker now. This hook deletes any `.harness/session-id-*.start` or `.reflected` it finds, whatever its age, and says how many on stderr. A marker was a pointer to a transcript, never the transcript, so deleting one loses nothing. Nothing else in `.harness/` is touched.

## Idle orchestration chain (V4 #23 task 4)

The hook then launches `orchestration_idle.py`, a cooldown-gated driver that turns idle time into a small, bounded push of work:

1. **discover-skills** — `discover_skills.py --cadence-check`: refresh discovery sources, self-throttled to the cadence (default 7d).
2. **adapt-pass1** — `adapt_skills.py --limit 3`: stage ≤3 enriched candidate JSONs for the `adapt-evaluator` (Pass-2, agent-side) to judge.

The chain's first step, `reflect-corpus`, mined five unseen transcripts a pass and filed what it found. It retired with task 199, when the regex miner stopped filing on its own.

The chain is gated by the `auto_orchestration.py` state (chain `idle_chain`, default cooldown 24h) and the `enable_idle_chain` toggle, so most boots are a fast no-op. When it does fire, it can exceed this hook's 30s SessionStart timeout, so the driver is launched **detached** and the hook returns immediately. Its results surface on the next session through the `memory-recall-session-start` briefing. Pass-2 is not run here: a hook can't dispatch a sub-agent.

## Failure modes (all soft)

- **python3 not on PATH** → exit 0 silently.
- **`.harness/` doesn't exist, or holds no markers** → nothing to clear.
- **`orchestration_idle.py` not found, or no vault resolves** → the chain is not launched.

## What it never does

- **Never blocks session start.**
- **Never mines a transcript or writes to the vault.**
- **Never touches a transcript, or any `.harness/` file but the retired markers.**

## Antigravity equivalent

Not registered on `agy`. There is no marker to clear there, and agy sessions reach the night's lesson compile through their own transcripts (`transcript_full.jsonl`), which it reads with their own bookmarks.

## See also

- [`lesson_compile.py`](../../skills/memory/scripts/lesson_compile.py) — the night's lesson compile, which replaced the orphan sweep.
- [`memory-reflect-stop`](../memory-reflect-stop/hook.md) — the Stop hook, which keeps each session's trace current.
- [`memory-recall-session-start`](../memory-recall-session-start/hook.md) — shares the SessionStart event; both fire on session boot.
- [MemoryVault reflection-and-recovery part](../../wiki/explanation/designs/memoryvault/parts/reflection-and-recovery.md) — full architectural context.
