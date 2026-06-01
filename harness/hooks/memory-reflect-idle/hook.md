---
name: memory-reflect-idle
description: "Idle-time / orphan-recovery hook that scans .harness/session-id-*.start markers for crashed sessions where the Stop hook never fired + runs reflection retroactively. Also GCs .reflected markers older than 30 days. Registered on SessionStart to fire on every session boot/resume (catches operator returning after a break); also invokable manually or via cron for periodic orphan sweeps. Plan #7a part 3 task 4 — new crickets primitive."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
install_scope: project
---

# memory-reflect-idle — orphan-recovery + idle reflection sweep

A standalone hook script that scans for **orphan session markers** in `.harness/` (created by SessionStart, renamed to `.reflected` by Stop on success). Orphans = `.start` markers older than the idle threshold (default 1 hour) — these represent sessions where Claude Code crashed before the Stop hook could fire reflection.

## Design rationale — why a "new primitive"

Claude Code doesn't expose a native "idle" hook event. The lifecycle events are `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` / etc. — all driven by agent activity. There's no "agent has been silent for N minutes" event.

The crickets's idle-time primitive works around this by:

1. **Crash-recovery markers** (lands in plan #7a part 3 task 6) — SessionStart writes `.harness/session-id-<uuid>.start`; Stop renames to `.reflected` on success. A `.start` marker that's still `.start` past the idle threshold = orphan = crashed session.
2. **Sweep on SessionStart resume/clear/compact** — this hook registers on `SessionStart` event so it fires whenever an operator comes back to a session. Catches the common case: "operator returns after a coffee break, session was idle, orphans from a previous crashed session get reflected retroactively."
3. **Manual / cron invocation** — for truly orphaned sessions (operator never returns), the script is callable directly: `bash .claude/hooks/memory-reflect-idle.sh` or `*/30 * * * * cd <project>; bash .claude/hooks/memory-reflect-idle.sh`.

This is honest about Claude Code's limitations. A future v2 with a real idle-event surface (Claude Code roadmap?) can replace the heuristic; the public contract of this hook stays stable.

## How it works

- **Trigger:** `SessionStart` event (matcher `.*` — fires on startup/resume/clear/compact). Coexists with `memory-recall-session-start` on the same event.
- **Idle threshold:** 1 hour (3600s) per locked design call B2.ii. Override via `MEMORY_IDLE_THRESHOLD_SEC` env var.
- **Orphan scan:** glob `.harness/session-id-*.start`; for each marker, check `mtime` against the idle threshold. Markers fresher than the threshold are skipped (session might still be active). Markers older than the threshold are orphans → run reflection.
- **Reflection retroactively:** read marker contents for transcript path, invoke `reflect.py <transcript> --summary` against it, rename `.start` → `.reflected` on success.
- **GC pass:** delete `.reflected` markers older than 30 days (default; matches B2.ii locked threshold).
- **Output:**
  - **stdout** — passes through reflect.py output per orphan (one JSON record per line; future task-5 routing will consume).
  - **stderr** — one transparency line if any orphans found: `[memory-reflect-idle] Scanned N markers; processed M orphans (idle threshold: <sec>s)`. Silent if no orphans + no markers.
- **Exit 0 always** — graceful-skip across missing reflect.py / no markers / unreadable marker / transcript missing / reflection error.

## Idle orchestration chain (V4 #23 task 4)

After the orphan sweep + GC, the hook launches the **idle orchestration chain** —
`orchestration_idle.py`, a cooldown-gated driver that turns idle time into a
small, bounded push of memory work:

1. **reflect-corpus** — `reflect.py corpus --execute --batch-size 5 --max-batches 1`: proactively mine ≤5 unseen session transcripts per pass.
2. **discover-skills** — `discover_skills.py --cadence-check`: refresh discovery sources, self-throttled to the cadence (default 7d).
3. **adapt-pass1** — `adapt_skills.py --limit 3`: stage ≤3 enriched candidate JSONs for the `adapt-evaluator` (Pass-2, agent-side) to judge.

The whole chain is gated by the `auto_orchestration.py` state (chain `idle_chain`,
default cooldown 24h) and the `enable_idle_chain` config toggle, so most boots are
a fast no-op. When it DOES fire, the work (corpus mining + GitHub enrichment) can
exceed this hook's 30s SessionStart timeout, so the driver is launched **detached**
(backgrounded, reparented away from the hook) and the hook returns immediately.
The chain's results surface on the **next** session via the `memory-recall-session-start`
briefing (it reads vault state, not the driver's stdout). Pass-2 is intentionally
NOT run here — a hook can't dispatch a sub-agent; the staged-candidate count is
handed off to a phase-dispatch / nudge where dispatch is operator-gated (DC-1).

The hook also runs a read-only **vec-index drift sweep** (`vec_index.py full-sync`,
V4 #37) after launching the chain — surfaces embedding drift to stderr, never blocks.

## Implementation status (task 4 of plan #7a part 3)

This task ships the hook **scaffold + orphan-sweep logic**. The markers themselves are written by task 6 (SessionStart extension to write `.start`, Stop extension to rename to `.reflected`). Task 5 wires the tri-modal routing — until then, the idle sweep emits reflection output but doesn't save candidates. Task 6 closes the marker-lifecycle loop.

## Marker file format

(Locked in task 6; documented here so the idle hook + task-6 extension agree.)

```
session_id: <uuid>
started_at: <iso-timestamp>
transcript: <absolute-path-to-transcript-jsonl>
```

The idle hook parses `transcript:` line for the reflection target. Other fields are operator-debug only.

## Failure modes (all soft)

- **reflect.py not installed** → exit 0 silently (graceful-skip; same as other memory hooks).
- **python3 not on PATH** → exit 0 silently.
- **`.harness/` doesn't exist** → exit 0 silently (no markers, nothing to do).
- **No `.start` markers** → exit 0 silently.
- **Marker file unreadable / missing `transcript:` line** → stderr warning + skip that marker.
- **Transcript path in marker doesn't exist** → stderr warning + skip that marker.
- **Reflection error on an orphan** → stderr warning + skip rename (marker stays `.start` for retry next pass).

## What it never does

- **Never blocks session start.** SessionStart hook contract preserved — exit 0 within budget, partial results on overrun.
- **Never deletes `.start` markers.** Only renames to `.reflected` on success. Failure leaves the marker for the next sweep.
- **Never writes to MemoryVault.** Reflection output flows to stdout; task 5 wires the actual save/inbox-write routing.

## Antigravity equivalent

Antigravity has no first-class hook surface; the equivalent pattern is a background daemon or scheduled task that does orphan recovery. Tracked under MemoryVault's discovery-mining part as a future companion customization.

## See also

- [`memory-reflect-stop`](../memory-reflect-stop/hook.md) — companion Stop-event hook that fires inline (not retroactively).
- [`memory-recall-session-start`](../memory-recall-session-start/hook.md) — shares the SessionStart event; both fire on session boot.
- [`reflect.py`](../../skills/memory/scripts/reflect.py) — canonical Python mining module invoked by this hook.
- [MemoryVault reflection-and-recovery part](../../wiki/explanation/designs/memoryvault/parts/reflection-and-recovery.md) — full architectural context.
