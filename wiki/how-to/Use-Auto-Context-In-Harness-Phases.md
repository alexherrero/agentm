# How to use auto-context in harness phases

> [!NOTE]
> **Goal:** Tune the harness's phase-boundary MemoryVault auto-context behavior (recall budgets, save mode, confidence threshold) for your project, and troubleshoot when it doesn't fire as expected.
> **Prereqs:** [crickets](https://github.com/alexherrero/crickets) sibling-cloned next to agentm; `MEMORY_VAULT_PATH` env set; harness ≥ v2.5.0 (when ADR 0007 shipped). See [Project config](Project-Config) for the `.harness/project.json` field references this page assumes.

Once [crickets](https://github.com/alexherrero/crickets) is sibling-cloned next to agentm and `MEMORY_VAULT_PATH` is set, every harness phase auto-loads relevant MemoryVault context at its natural start, and offers to save durable items at its natural end — without you having to invoke `/memory search` or `/memory save` manually.

This page covers: what loads/saves at each phase boundary, the env vars that tune the behavior, and how to troubleshoot when something feels off.

## Prerequisites

1. **MemoryVault installed** (v4.0.0+: shipped with agentm at `harness/skills/memory/`; in v3.x and earlier it lived at `crickets/skills/memory/` and the harness loaded it via sibling-clone resolution). For v3.x compatibility, the harness's 3-tier resolver checks `agentm/harness/skills/memory/scripts/save.py` first, then falls back to the legacy `crickets/skills/memory/scripts/save.py` sibling path, then to `HARNESS_MEMORY_TOOLKIT_PATH` env override.
2. **`MEMORY_VAULT_PATH` env set** to your vault root (e.g. `~/Library/CloudStorage/GoogleDrive-…/Obsidian/AgentMemory`).
3. **`.harness/project.json` has a `vault_project` field** OR your repo has a `github.repo` field OR a git origin — auto-detect uses the 3-tier fallback (see [ADR 0007](0007-auto-context-into-harness-phases) §Q2).

If any prerequisite is absent, every phase still works — the dispatcher graceful-skips silently (see Troubleshooting below).

## Per-phase boundaries

| Phase | Recall (start) | Save (end) |
|---|---|---|
| `/setup` (§1b + §8b) | `_always-load/` conventions | Offer `projects/<slug>/_index.md` stub (legacy `personal-projects/` accepted pre-rename) |
| `/plan` (§1b + §4c) | `_always-load/` + `_index.md` + decisions + open-questions | Offer per-entry save for plan's `## Risks / open questions` |
| `/work` (§1b + §7b + §7c) | `_always-load/` + decisions + known-issues | Offer "remember-this" candidates + `plan-done-promotion` when final task flips PLAN.md to `done` |
| `/review` (§2b) | `_always-load/` only (read-only — no save) | — |
| `/release` (§1c + §5b + §5c) | `_always-load/` + decisions | Offer per-decision save + `plan-done-promotion` (shared cursor with `/work`) |
| `/bugfix` (§2b + §4b) | `_always-load/` + known-issues | Offer save when bug had non-obvious root cause |

The "Pattern A" boundary is recall-then-work-then-offer-save. `/review` skips save by design — a reviewer that writes biases toward confirming its own findings.

## The dispatcher

All phase specs invoke `python3 scripts/harness_memory.py` with one of four sub-commands:

```bash
# Check vault availability — exit 0 if accessible, 1 otherwise:
python3 scripts/harness_memory.py available

# Phase-specific recall (graceful-skip on absent vault):
python3 scripts/harness_memory.py recall --phase <name> --project <slug>
                                          [--budget <tokens>] [--permanent-only]

# Self-modulating offer-save (graceful-skip on absent vault):
python3 scripts/harness_memory.py offer-save \
    --phase <name> --project <slug> \
    --kind <decision|gotcha|workflow|...> --slug <entry-slug> \
    --content-file <path> \
    [--confidence <0-1>] [--confidence-reason <text>]

# Cursor-tracked progress.md tail-scan (idempotent re-invocation):
python3 scripts/harness_memory.py plan-done-promotion --project-root . [--dry-run]
```

You don't normally invoke these directly — phase specs invoke them at the right moments. Manual invocation is for debugging.

## Env-var matrix

| Variable | Default | Effect |
|---|---|---|
| `MEMORY_VAULT_PATH` | (unset) | Vault root path. **Unset → all auto-context features graceful-skip silently.** |
| `HARNESS_AUTO_SAVE_MODE` | `ask` | `ask` (confidence-modulated), `silent` (always save, no prompt), `off` (never save). |
| `HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` | `0.8` | Float 0–1. Agent-supplied `--confidence ≥ threshold` → silent save with stderr notice; below → prompt. |
| `HARNESS_RECALL_BUDGET_<PHASE>` | 4000 (review/setup) or 6000 (others) | Token cap for recall, per phase. Use uppercase phase name: `SETUP` / `PLAN` / `WORK` / `REVIEW` / `RELEASE` / `BUGFIX`. |
| `HARNESS_MEMORY_TOOLKIT_PATH` | (auto-detect) | Override the toolkit memory-scripts dir. Used by tests + by operators with non-standard toolkit install locations. |

## Worked scenario: tuning offer-save fatigue

**Symptom:** every `/work` task ends with 3+ "save this entry? [y/N]" prompts. You're reflex-skipping all of them.

**Diagnosis:** the agent's confidence calibration is below your threshold for everything, so every candidate fires the prompt. Either the agent is being conservative (good signal, bad UX), or the threshold is too high for early dogfood.

**Three fixes, increasing aggressiveness:**

1. **Lower the threshold:** `export HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD=0.7`. High-confidence candidates now silent-save; only genuinely-ambiguous ones prompt.
2. **Cap candidates in `/work` §7b:** the spec already caps at ~3 candidates per session. If the agent is firing more, that's a signal to widen scope at `/plan` rather than dump to the vault.
3. **Switch to silent globally:** `export HARNESS_AUTO_SAVE_MODE=silent`. Auto-save runs; you scan stderr `[auto-saved high-confidence]` lines periodically and `/memory evolve` any that turned out wrong.

Reverse direction: if you're getting false-positive silent saves (vault filling with noise), raise the threshold: `export HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD=0.9`.

## Worked scenario: recall budget feels too tight

**Symptom:** `/plan` recall returns 2 entries when you know there are 8 relevant decisions in the vault for this project.

**Diagnosis:** budget cap is dropping trailing entries. Confirm with the recall header line: `(budget: ~6000 tokens, entries: 2)` — if entries < what you expect, budget is constraining.

**Fix:** `export HARNESS_RECALL_BUDGET_PLAN=12000` (double the default). Re-run `/plan`. The header will reflect the new budget.

Entry cap is a separate constraint (default 5 per phase) — if you need more entries, that's a phase-spec config in `harness_memory.py` rather than env.

## Worked scenario: confirming the plan-done-promotion cursor

**Symptom:** you ran `/release` and the tail-scan returned empty even though progress.md has 20 entries since the last release.

**Diagnosis:** likely the cursor was already advanced by `/work`'s plan-done-promotion when you marked the final task `[x]`. Per Q5 design call, the cursor is shared between `/work` §7c + `/release` §5c — single fire per plan-window.

**Confirm:** `cat .harness/.promoted-progress-cursor` should show a byte offset at or near `wc -c .harness/progress.md` output. If yes, the cursor is at EOF — promotion already happened.

**Reset (if you want to re-promote):** `rm .harness/.promoted-progress-cursor`. Next `plan-done-promotion` invocation will re-emit the full tail. The toolkit's `save.py` deduplicates so the worst case is a few re-prompts for already-saved candidates.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No recall output at all | `MEMORY_VAULT_PATH` env unset OR directory missing | `echo $MEMORY_VAULT_PATH` + verify dir exists |
| Recall output but missing per-project entries | `vault_project` slug not resolving to a real `projects/<slug>/` dir (or legacy `personal-projects/<slug>/` pre-rename) | `python3 scripts/vault_project.py read .` — check the returned slug matches a vault entry |
| `[harness_memory] toolkit not installed` stderr notice | Memory scripts not found via 3-tier resolution | Verify `agentm/harness/skills/memory/scripts/save.py` (v4.0.0+) OR legacy `crickets/skills/memory/scripts/save.py` (v3.x) exists; OR set `HARNESS_MEMORY_TOOLKIT_PATH` |
| Save prompt fires even at high confidence | Threshold set above 0.8 OR `HARNESS_AUTO_SAVE_MODE=ask` with no `--confidence` passed | Check threshold env; if confidence is omitted by the agent, prompt is correct behavior (fallback to ask) |
| Save proceeds silently when you wanted to review | `HARNESS_AUTO_SAVE_MODE=silent` OR confidence ≥ threshold | Switch mode back to `ask` (default); raise threshold if confidence is being over-estimated |
| Cursor advances but no candidates surface | `progress.md` since last cursor was empty OR LLM summarizer found nothing durable | Expected when last plan was small/routine; re-check with `--dry-run` flag |
| Windows UnicodeEncodeError on recall output | cp1252 stdout default; recall output contains non-ASCII | Add `sys.stdout.reconfigure(encoding="utf-8")` defensively in any wrapper script invoking the dispatcher |
| `available` exits 1 even with vault set | Vault directory deleted/moved OR permissions issue | `ls -la "$MEMORY_VAULT_PATH"` — confirm it's readable + a directory (not a symlink to nowhere) |

## See also

- [Repo-Layout](Repo-Layout) — where `scripts/harness_memory.py` + `scripts/vault_project.py` live in the harness tree.
- [CI-Gates](CI-Gates) — the unit tests that exercise the dispatcher cross-platform on Linux/Mac/Windows.
- [ADR 0007 — Auto-context into harness phases](0007-auto-context-into-harness-phases) — 5 locked design calls + load-bearing assumptions.
- [crickets Cross-Repo Memory Protocol](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/Cross-Repo-Memory-Protocol.md) — toolkit-side contract documentation.
- [crickets `/memory` skill](https://github.com/alexherrero/crickets/blob/main/skills/memory/SKILL.md) — the underlying save/recall surface that `harness_memory.py` shells out to.
- Phase specs: [01-setup](https://github.com/alexherrero/agentm/blob/main/harness/phases/01-setup.md) · [02-plan](https://github.com/alexherrero/agentm/blob/main/harness/phases/02-plan.md) · [03-work](https://github.com/alexherrero/agentm/blob/main/harness/phases/03-work.md) · [04-review](https://github.com/alexherrero/agentm/blob/main/harness/phases/04-review.md) · [05-release](https://github.com/alexherrero/agentm/blob/main/harness/phases/05-release.md) · [bugfix](https://github.com/alexherrero/agentm/blob/main/harness/pipelines/bugfix.md).
