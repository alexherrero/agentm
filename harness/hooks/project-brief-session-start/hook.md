---
name: project-brief-session-start
description: SessionStart hook that opens a session on where its bound project and task stand, in under twenty lines — the project tracker's and the task tracker's State and Next, the last three progress lines, the count of open follow-ups and of unfiled captures carrying the project — rendered by `scripts/project_brief.py`. With no tracker it prints today's plan block, rendered by harness-context-session-start itself (`AGENTM_PLAN_BLOCK_ONLY=1`), and once registered that hook leaves its plan block here so nothing prints twice. Kept apart from the always-load hook, whose output the host collapses unread. Never blocks boot. agentm-vault plan 09.
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# project-brief-session-start — a session opens on where its project stands

A `SessionStart` hook. agentm-vault § Projects and tasks decides that a session bound to a project opens on twenty lines: what is true now and what comes next, for the project and for the task, read from the two trackers rather than assembled from the tail of a plan and a progress log. This hook prints them.

## Behavior

- **Reads `cwd` from the SessionStart event JSON on stdin**, like harness-context-session-start, and resolves `scripts/project_brief.py` from `~/.claude/.agentm-config.json` → `source_clones.agentm`, falling back to `~/Antigravity/agentm`.
- **The binding** is the directory's `.harness/project.json` (the vault project) and `.harness/active-plan` (the task), read by `session_binding.py`. An unbound directory has no brief.
- **The brief** (at most 20 lines, each clipped to 160 characters): a header naming the project and task, the project tracker's State (two lines) and Next (one), the task tracker's status, State (four lines) and Next (three), the last three progress lines, the count of open follow-ups (the project's `followups.md`: unchecked boxes and table rows not marked ✅) and of `status: unfiled` cards carrying this `project:`, and the plan's path. The header asks the session to pass the project and task when it captures, which is how the in-session writer stamps them.
- **The fallback.** With no tracker for the task or the project, `project_brief.py` prints nothing and exits 3, and this hook runs harness-context-session-start with `AGENTM_PLAN_BLOCK_ONLY=1`: that hook's own plan block — the named-plan block, the singleton block, or the setup nudge — and nothing after it. Before the migration writes trackers, a session therefore opens on exactly the lines it opens on today.
- **No duplicate block.** While this hook is registered in the user or project settings, harness-context-session-start prints no plan block of its own; it still prints its observability line.
- **Budget:** 2 seconds for the brief, via `gtimeout`/`timeout` when present. Every failure degrades to the fallback or to silence.

## Output

```
[agentm] agentm · task build-the-brief — pass both as project and task when you capture
Project state: Plan 09 is building; plan 07 closes first.
Project next: Merge plan 07, then rebase plan 09.
Task state (active): Tasks 1-5 shipped in the worktree.
  The draft PR is open; CI is green.
Task next: Wait for plan 07's close-out.
Recent progress:
  2026-09-12 23:05 PDT /work — completed task 3
  ...
Open follow-ups: 8 · unfiled captures with project agentm: 2
Plan: <vault>/projects/agentm/tasks/build-the-brief/plan.md
```

## Install

The install's hook list merges this directory's settings fragment like every other hook's. On a source-mode install, register it by hand once the clone carries it:

```bash
ln -s ~/Antigravity/agentm/harness/hooks/project-brief-session-start ~/.claude/hooks/project-brief-session-start
python3 ~/Antigravity/agentm/scripts/merge-settings-fragment.py ~/.claude/settings.json ~/Antigravity/agentm/harness/hooks/project-brief-session-start/settings-fragment-bash.json --command "bash $HOME/.claude/hooks/project-brief-session-start/project-brief-session-start.sh"
```

## Related

- `scripts/project_brief.py` — the renderer; `scripts/tracker.py` — the tracker schema.
- `harness/hooks/harness-context-session-start/` — the plan block this hook falls back to.
- agentm-vault § Projects and tasks, "A session opens on twenty lines".
