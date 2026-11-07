---
name: memory-automemory-note
description: "PostToolUse hook, registered once machine-wide on Write, Edit and MultiEdit, that records each note a Claude Code session saves to its auto-memory folder (~/.claude/projects/<folder>/memory/) as an input to that night's lesson compile. It files nothing itself. Fails open."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# memory-automemory-note — hand auto-memory to the night

Claude Code keeps its own memory per project folder, at `~/.claude/projects/<folder>/memory/*.md`. Until task 199 none of it reached the vault. This hook closes that gap without opening a second filing path.

## What it does

On every `Write`, `Edit` or `MultiEdit`, the hook checks whether the file written sits in an auto-memory folder. If it does, it appends one line to the lesson compile's input queue in the engine's state directory (`lesson-compile/automemory.jsonl`): the note's path, its folder, the session and the time. The index file, `MEMORY.md`, is not a note and is skipped.

That night, the lesson compile maps the folder to its vault project, reads each queued note as it stands then, and treats it as one more input to that project's day, beside the session slices, commits and traces. The compile decides what, if anything, becomes a lesson, and it dedupes against the vault first. So a note saved here counts toward the project's daily limit like anything else, and it cannot reach the vault on its own.

Notes saved before the hook existed are folded once by `lesson_compile.py backfill-automemory`, outside the daily limit.

## Cost

A shell pre-filter turns away every payload that doesn't name an auto-memory folder before any Python starts, so an ordinary write costs a string match. Python starts only for a write that names one of those folders.

## Fails open

No payload, no `python3`, or no `lesson_compile.py` it can find: the hook exits 0 and the write goes ahead. A queue line it could not write is lost; the backfill pass, or the next save of the same note, still carries it.

## Antigravity equivalent

None. Antigravity keeps no per-project auto-memory folder, and its sessions reach the compile through their own transcripts.

## See also

- [`lesson_compile.py`](../../skills/memory/scripts/lesson_compile.py) — the night's compile, and `note-automemory`, the entry this hook calls.
- [`memory-reflect-stop`](../memory-reflect-stop/hook.md) — the Stop hook, which keeps each session's trace current.
