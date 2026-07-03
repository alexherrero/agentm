# Auto-orchestration config reference

The two files that drive the memory push-surface: the operator-tunable config at `<vault>/personal/auto-orchestration-config.md` (toggles, thresholds, cooldowns — auto-seeded with defaults, re-seed never clobbers operator edits) and the runtime state at `<vault>/_meta/auto-orchestration-state.json` (last-fire-per-chain timestamps plus the last-shown snapshot for the shifted-since-last-shown check). All keys live in `DEFAULT_CONFIG` in [`auto_orchestration.py`](https://github.com/alexherrero/agentm/blob/main/harness/skills/memory/scripts/auto_orchestration.py). For the why, see [Auto-orchestration](Auto-Orchestration); to edit the config, see [Tune auto-orchestration](Tune-Auto-Orchestration).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where is the operator config? | `<vault>/personal/auto-orchestration-config.md` — auto-seeded, operator-editable. |
| Where is the runtime state? | `<vault>/_meta/auto-orchestration-state.json` — `last_fire` timestamps + `last_shown` snapshot. |
| Does a re-seed overwrite my edits? | No. `seed_config` is idempotent and never clobbers an existing file. |
| What are the four config groups? | Emission toggles · briefing thresholds · nudge thresholds · chain cooldowns. |
| What does the state file gate? | Cooldown windows (`should_fire(chain, now)`) and the shifted-since-last-shown briefing check. |
| Who writes the state file? | `auto_orchestration.py` alone — via `save_state()`. No other script in `orchestration_*.py` may call `save_state` directly (V5-5 single-writer invariant, LC-2; enforced by `verify-v4.sh` segment G). |
| Related pages | [Auto-orchestration](Auto-Orchestration), [Tune auto-orchestration](Tune-Auto-Orchestration) |

## Emission toggles

Each `enable_*` toggle turns one emission off entirely (a `false` value short-circuits that chain before any work). Set with `true` / `false`.

| Key | Controls | Default |
|---|---|---|
| `enable_briefing` | the SessionStart pending-state briefing | `true` |
| `enable_idle_chain` | the idle-time orchestration chain | `true` |
| `enable_phase_integration` | post-`/work` reflect + post-`/release` skill refresh | `true` |
| `enable_promote_suggest` | the "you keep having this idea — promote it?" nudge | `true` |
| `enable_stale_promotion_nudge` | the stale-`promoted` watchlist safety-rail nudge | `true` |

## Briefing thresholds

The counts that decide whether the SessionStart briefing reports a section. A signal surfaces only at or above its threshold.

| Key | Controls | Default |
|---|---|---|
| `inbox_threshold` | `_inbox/*.md` count at or above which the briefing flags it | `10` |
| `watchlist_high_threshold` | `_skill-watchlist/` HIGH + `pending-review` count to surface | `1` |
| `incubator_pending_threshold` | `_idea-incubator/<slug>/` dirs pending research to surface | `1` |
| `idea_ledger_stale_months` | months after which an Ideas-ledger entry is GC-eligible | `6` |

## Nudge thresholds

The thresholds applied inside the two nudge counters before a nudge surfaces.

| Key | Controls | Default |
|---|---|---|
| `promote_mention_threshold` | times a single idea must recur in the Ideas ledger to suggest `/memory promote` | `3` |
| `stale_promotion_days` | days a `_skill-watchlist/` entry can sit `status: promoted` before the nudge fires | `30` |

## Chain cooldowns

The minimum window (hours) between fires of each chain, honored by `should_fire(chain, now)`. A non-positive value means "always eligible".

| Key | Controls | Default |
|---|---|---|
| `briefing_cooldown_hours` | min gap between SessionStart briefing emissions (chain `briefing`) | `8` |
| `idle_chain_cooldown_hours` | min gap between idle-chain runs (chain `idle_chain`) | `24` |
| `phase_reflect_cooldown_hours` | min gap for the phase-integration dispatches (chains `phase_reflect` + `phase_release`) | `1` |

## Idle-chain steps

The bounded steps the idle chain runs in order. The whole chain is gated by `enable_idle_chain` + the `idle_chain` cooldown — there is no per-step config toggle. Each underlying script self-no-ops when its input is empty.

| Step | What it runs | Bound |
|---|---|---|
| `reflect-corpus` | `reflect.py corpus --execute` — mine unseen session transcripts | `--batch-size 5 --max-batches 1` (≤5 sessions/pass) |
| `discover-skills` | `discover_skills.py --cadence-check` — refresh discovery sources | cadence-check (self-throttled) |
| `adapt-pass1` | `adapt_skills.py --limit 3` — stage Pass-1 candidate JSONs | `--limit 3` |

> [!NOTE]
> Pass-2 (the `adapt-evaluator` sub-agent) is **not** an idle-chain step. A hook fires outside the agent loop and cannot dispatch a sub-agent, so the chain stages Pass-1 candidates and surfaces the staged count (`staged_candidates` in the run result); the Pass-2 hand-off lands via phase-dispatch / nudge where dispatch is operator-gated.

## State file shape

`<vault>/_meta/auto-orchestration-state.json` — written exclusively by `auto_orchestration.py:save_state()` (V5-5 LC-2 single-writer invariant). No other script writes this file directly; sibling orchestration scripts call through `ao.save_state()`. Not tracked by git. JSON with two top-level objects.

| Field | Type | Meaning |
|---|---|---|
| `last_fire` | object | `{<chain>: <ISO-8601 timestamp>}` of the most recent fire per chain; read by `should_fire`. Chains: `briefing`, `idle_chain`, `phase_reflect`, `phase_release`. |
| `last_shown` | object | `{<signal>: <count>}` snapshot of the over-threshold signals the briefing last displayed; `state_shifted_since_last_shown` compares against it to suppress re-firing until a count changes. |

## Related

- [Auto-orchestration](Auto-Orchestration) — what these knobs change and why the surface never nags.
- [Tune auto-orchestration](Tune-Auto-Orchestration) — the recipe for editing the config.
- [AgentMemory context payload](AgentMemory-Context-Payload) — the vault folder map (`_inbox/`, `_idea-incubator/`, `_meta/`, `personal/`) these files reference.
