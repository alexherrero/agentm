---
name: harness-context-session-start
description: SessionStart hook that injects the project's vault-resident PLAN.md + progress.md paths into session context. Fires on every session boot in any cwd; resolves the active project's harness state via `harness_memory.py vault-state-path` and emits a short 4-line block only when both files exist. Enumerates `PLAN-<name>.md` siblings and, when ≥1 exists, emits a named-plan block listing every active plan + the `.harness/active-plan` binding instead — falling through to the byte-identical singleton block when none exist (V5-10). When state does NOT resolve but the cwd is an unconfigured git repo, emits a one-line auto-detect nudge instead (V4 #32, via `project_config.py should-nudge`). Silent no-op for non-harness/non-git cwds. Hard 500ms budget; degraded-graceful. V4 #39 + #32 + V5-10.
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
install_scope: user
---

# harness-context-session-start — surface vault PLAN.md / progress.md on session boot

A `SessionStart` event hook (universal, `install_scope: user` — fires in every project the operator works in). Post-V4 #26, a project's harness state (`PLAN.md`, `progress.md`) lives in the MemoryVault at `<vault>/projects/<slug>/_harness/`, not in the repo's `.harness/`. Nothing told the agent that at session boot — it had to *think* to call the resolver, and sometimes didn't (the gap that motivated V4 #39). This hook closes it: on every SessionStart it reads the event's `cwd`, resolves the active project's vault state paths via `harness_memory.py vault-state-path`, and — **only when both `PLAN.md` and `progress.md` resolve and exist on disk** — injects a 4-line context block telling the agent where they live and to read `PLAN.md` before plan-status questions or phase commands.

## Behavior

- **Reads `cwd` from the SessionStart event JSON on stdin** (not the script's `pwd` — Claude Code's hook-firing cwd may differ from the project cwd; DC-6).
- **Resolves `harness_memory.py`** from `~/.claude/.agentm-config.json` → `source_clones.agentm`, falling back to `~/Antigravity/agentm/scripts/harness_memory.py`.
- **Injects only when both state files exist** — otherwise a silent `exit 0` with a one-line stderr reason. Non-harness cwds, an unreachable vault, or a missing resolver all degrade gracefully (DC-3).
- **Named-plan discovery (V5-10).** Before the singleton check, the hook enumerates `PLAN-<name>.md` files in the resolved `_harness/` dir (the parent of the `vault-state-path PLAN.md` result — pure path construction, so it resolves even when the unnamed `PLAN.md` is absent), skipping GDrive `*(conflicted copy*` copies. When ≥1 named plan exists it emits the **named-plan block** (below) listing every `PLAN*.md` and the `.harness/active-plan` binding; when zero exist it falls through to the **locked singleton block**, byte-identical to pre-V5-10 — so a solo repo is unchanged (back-compat). A present `.harness/active-plan` marker naming an absent `PLAN-<name>.md` is surfaced as `DANGLING`, never fatal (the hook reports; `resolve_active_plan` is the loud-error enforcer at bind time).
- **Auto-detect nudge (V4 #32).** When state does NOT resolve, the hook asks `project_config.py should-nudge` whether this cwd is an unconfigured project worth offering setup to (gate: has `.git` AND not registered AND no `.agentm-no-register` marker AND not a harness-source bypass). If so, it emits a single line — `[agentm] New project — I haven't configured this repo. Say 'configure this project' or run /setup --detect.` — instead of staying silent. All nudge logic lives in testable Python; the hook only emits. The nudge fires each unconfigured session until the repo is registered or `.agentm-no-register` is dropped.
- **Hard 500ms budget** via `gtimeout`/`timeout` when available (graceful if neither is installed).
- **Fires on matcher `.*`** (every SessionStart — startup / resume / clear / compact). Idempotent: re-injecting the same block on resume is harmless (DC-8).
- **Output block — singleton** (4 lines, locked DC-7; emitted when zero named plans exist):

  ```
  [agentm] Project state for this repo lives in the vault, not in .harness/:
    PLAN.md:     <resolved path>
    progress.md: <resolved path>
  Read PLAN.md before answering plan-status questions or running /work, /review, /release.
  ```

- **Output block — named-plan mode** (additive; emitted when ≥1 `PLAN-<name>.md` exists). Keeps the `[agentm] Project state` opening (the `--live` doctor probe asserts that substring), then lists every plan and the active binding:

  ```
  [agentm] Project state for this repo lives in the vault, not in .harness/:
  Named-plan mode - this repo has more than one active plan:
    PLAN.md                <resolved path>          (only if the unnamed singleton also exists)
    PLAN-foo.md            <resolved path>
    PLAN-bar.md            <resolved path>
  Active plan (.harness/active-plan -> foo): <resolved path>   (or "DANGLING - PLAN-foo.md not found" when unresolvable)
  Read the plan you own (or the .harness/active-plan one) before /work, /review, /release.
  ```

## Install

Universal — installs to `~/.claude/hooks/harness-context-session-start/` under `--scope user`; the installer merges `settings-fragment-bash.json` into `~/.claude/settings.json` and absolutizes the command to `bash ~/.claude/hooks/harness-context-session-start/harness-context-session-start.sh` (V4 #39 task 1). Never blocks session boot.
