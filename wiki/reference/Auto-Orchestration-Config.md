# Auto-orchestration config reference

Two files drive the memory push-surface. You tune the config at `<vault>/personal/auto-orchestration-config.md`. It holds toggles, thresholds, and cooldowns. It auto-seeds with defaults. Re-seeding never clobbers your edits. The system stores runtime state at `<vault>/_meta/auto-orchestration-state.json`. This state holds last-fire-per-chain timestamps. It also holds the last-shown snapshot for the shifted-since-last-shown check. All keys live in `DEFAULT_CONFIG` in [`auto_orchestration.py`](https://github.com/alexherrero/agentm/blob/main/harness/skills/memory/scripts/auto_orchestration.py). See [Auto-orchestration](Auto-Orchestration) for the why. See [Tune auto-orchestration](Tune-Auto-Orchestration) to edit the config.

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

Each `enable_*` toggle turns one emission off entirely. A `false` value short-circuits that chain before any work. You set these with `true` or `false`.

| Key | Controls | Default |
|---|---|---|
| `enable_briefing` | the SessionStart pending-state briefing | `true` |
| `enable_idle_chain` | the idle-time orchestration chain | `true` |
| `enable_phase_integration` | post-`/work` reflect + post-`/release` skill refresh | `true` |
| `enable_promote_suggest` | the "you keep having this idea — promote it?" nudge | `true` |
| `enable_stale_promotion_nudge` | the stale-`promoted` watchlist safety-rail nudge | `true` |

## Briefing thresholds

These counts decide whether the SessionStart briefing reports a section. A signal surfaces only at or above its threshold.

| Key | Controls | Default |
|---|---|---|
| `inbox_threshold` | `_inbox/*.md` count at or above which the briefing flags it | `10` |
| `watchlist_high_threshold` | `_skill-watchlist/` HIGH + `pending-review` count to surface | `1` |
| `incubator_pending_threshold` | `_idea-incubator/<slug>/` dirs pending research to surface | `1` |
| `idea_ledger_stale_months` | months after which an Ideas-ledger entry is GC-eligible | `6` |

## Nudge thresholds

These thresholds apply inside the two nudge counters before a nudge surfaces.

| Key | Controls | Default |
|---|---|---|
| `promote_mention_threshold` | times a single idea must recur in the Ideas ledger to suggest `/memory promote` | `3` |
| `stale_promotion_days` | days a `_skill-watchlist/` entry can sit `status: promoted` before the nudge fires | `30` |

## Chain cooldowns

This is the minimum window in hours between fires of each chain. The `should_fire(chain, now)` check honors it. A non-positive value means "always eligible".

| Key | Controls | Default |
|---|---|---|
| `briefing_cooldown_hours` | min gap between SessionStart briefing emissions (chain `briefing`) | `8` |
| `idle_chain_cooldown_hours` | min gap between idle-chain runs (chain `idle_chain`) | `24` |
| `phase_reflect_cooldown_hours` | min gap for the phase-integration dispatches (chains `phase_reflect` + `phase_release`) | `1` |

## Idle-chain steps

The idle chain runs these bounded steps in order. The `enable_idle_chain` toggle and the `idle_chain` cooldown gate the whole chain. You cannot toggle steps individually. Each underlying script self-no-ops when you give it empty input.

| Step | What it runs | Bound |
|---|---|---|
| `reflect-corpus` | `reflect.py corpus --execute` — mine unseen session transcripts | `--batch-size 5 --max-batches 1` (≤5 sessions/pass) |
| `discover-skills` | `discover_skills.py --cadence-check` — refresh discovery sources | cadence-check (self-throttled) |
| `adapt-pass1` | `adapt_skills.py --limit 3` — stage Pass-1 candidate JSONs | `--limit 3` |

> [!NOTE]
> Pass-2 (the `adapt-evaluator` sub-agent) is **not** an idle-chain step. A hook fires outside the agent loop. It cannot dispatch a sub-agent. The chain stages Pass-1 candidates instead. It surfaces the staged count (`staged_candidates` in the run result). The Pass-2 hand-off lands via phase-dispatch or nudge. You gate dispatch there.

## State file shape

`auto_orchestration.py:save_state()` writes `<vault>/_meta/auto-orchestration-state.json` exclusively. This enforces the V5-5 LC-2 single-writer invariant. No other script writes this file directly. Sibling orchestration scripts call through `ao.save_state()` instead. Git does not track it. It holds JSON with two top-level objects.

| Field | Type | Meaning |
|---|---|---|
| `last_fire` | object | `{<chain>: <ISO-8601 timestamp>}` of the most recent fire per chain; read by `should_fire`. Chains: `briefing`, `idle_chain`, `phase_reflect`, `phase_release`. |
| `last_shown` | object | `{<signal>: <count>}` snapshot of the over-threshold signals the briefing last displayed; `state_shifted_since_last_shown` compares against it to suppress re-firing until a count changes. |

## Related

- [Auto-orchestration](Auto-Orchestration) explains what these knobs change. It shows why the surface never nags.
- [Tune auto-orchestration](Tune-Auto-Orchestration) provides the recipe for editing the config.
- [AgentMemory context payload](AgentMemory-Context-Payload) details the vault folder map. These files reference the `_inbox/`, `_idea-incubator/`, `_meta/`, and `personal/` paths.
