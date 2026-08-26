---
name: compaction-marker
description: "PreCompact hook, registered once machine-wide, that appends a dated compaction marker to the active plan's progress log so entries above it read as written-before-the-context-was-lost. Resolves the progress file through the process seam (vault or repo-local, singleton or named plan) and writes via write-state, which routes through the vault write lock. Silent no-op outside a harness project."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# compaction-marker — record that the context was lost here

Compaction discards the conversation. This leaves a dated marker in the project's progress log, so the entries above it are readable as *"written before the context was lost"* rather than as one continuous history by an author who remembers all of it.

## When it earns its keep

Mostly on **automatic** compaction. A deliberate `/compact` is a choice the operator remembers making. An automatic one fires mid-task with nothing said, and the next session reads recent progress entries written by a session that can no longer explain them.

This repo's own standing doctrine is `/clear` at phase boundaries rather than `/compact` — which makes auto-compaction, the case nobody chose, the one this hook exists for.

## What changed from the retired per-project version

The predecessor required `.harness/progress.md` **relative to the cwd** and appended to it directly. All three assumptions are now wrong:

- **State may not be in the repo.** The state-mode axis puts `progress.md` in the MemoryVault or in a device-local `.harness/`. The path is resolved via `harness_memory.py resolve-active-plan`, which answers correctly either way.
- **The singleton may not be the active plan.** With named plans the log is `progress-<slug>.md`. Taking the basename of what the seam resolved keeps that case right.
- **A direct append bypasses the vault write lock.** Writing goes through `write-state`, which routes via `vault_lock.atomic_write`, so the marker cannot race the daemon or another session writing the same log.

It also reads the **event's** `cwd` rather than `$PWD` (DC-6) — a hook's own working directory is not reliably the session's.

## Failure posture

Every unresolvable step exits 0 silently: no `python3`, no resolver, a non-harness directory, an uninitialized project, a read that returns nothing. A hook that blocks a compaction is worse than a missing marker.
