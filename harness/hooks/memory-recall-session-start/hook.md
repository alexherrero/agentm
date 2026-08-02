---
name: memory-recall-session-start
description: SessionStart hook that loads `MemoryVault/personal-private/_always-load/*.md` entries into session context. Fires once per session boot; emits "Loaded N MemoryVault always-load entries" transparency line. Hard time budget 500ms with degraded-graceful overrun. Plan #7a part 2 of MemoryVault Core.
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
install_scope: project
---

# memory-recall-session-start — load always-load MemoryVault entries on session boot

A `SessionStart` event hook that globs `MemoryVault/personal-private/_always-load/*.md`, reads each entry, and injects the bodies into the session as additional context. The agent sees the always-load entries before processing any user input.

## How it works

- **Trigger:** Claude Code's `SessionStart` event (matcher `.*` — fires on startup, resume, clear, and compact).
- **Vault resolution:** reads `MEMORY_VAULT_PATH` env var. If unset, exits 0 silently (no-op — the hook never breaks a session where MemoryVault isn't configured).
- **Glob:** `<vault>/personal-private/_always-load/*.md`.
- **Filter:** entries with `status: superseded` in their frontmatter are skipped (defense-in-depth; supersession normally moves entries to `_archive/` but we filter here too).
- **Output:**
  - **stdout** — formatted markdown block: a header line + each entry's body (frontmatter stripped) separated by `---` rules. Claude Code injects this as additional session context.
  - **stderr** — one transparency line: `[memory-recall-session-start] Loaded N MemoryVault always-load entries: <slug-list>` (shown to operator in hook logs but not the agent's context).
- **Time budget:** 500ms wall clock. On overrun: log a warning to stderr, return the partial results gathered so far (degraded-graceful). The hook never blocks session boot.
- **Exit 0** always (unless a Python interpreter error — vault problems are warnings, not failures).

## Crash-recovery marker

Before the recall pass, the hook writes `.harness/session-id-<session_id>.start` — the pointer [`memory-reflect-idle`](../memory-reflect-idle/hook.md)'s orphan sweep uses to reflect sessions where `Stop` never fired (crash, force quit, OS kill).

```
session_id: <uuid>
started_at: <iso-timestamp>
source: startup | resume | clear | compact | fork | unknown
transcript: <absolute-path-to-transcript-jsonl>
```

- **`transcript:`** is the payload's own **`transcript_path`**, which Claude Code sends on every hook event. Claude Code's hook docs direct hooks to use it rather than constructing a path from `session_id`, since that correspondence is not guaranteed. When the payload omits it (a build predating the field), the hook falls back to the computed `~/.claude/projects/<cwd-slug>/<session_id>.jsonl` — `cwd` with both `/` and `.` replaced by `-`, no extra leading `-`.
- **`source:`** records which `SessionStart` fired. Nothing reads it; it is there so a marker pointing somewhere unexpected reads as an explanation rather than a mystery. Defaults to `unknown` when the payload omits it.
- **Writes are idempotent and best-effort.** `SessionStart` re-fires on resume, clear and compact, so an existing marker is left untouched; a failed write never blocks the recall pass.

## What it never does

- **Never blocks session start.** If anything fails (vault missing, Python missing, entries unreadable), the hook exits 0 and the session proceeds without injected memory.
- **Never reads outside `<vault>/personal-private/_always-load/`.** Group-scoped `_always-load/` directories (e.g. `<vault>/work-public/_always-load/`) are reserved for future per-group recall; v0.1.0 hardwires `personal-private/`.
- **Never writes to the vault.** Pure read-only.
- **Never modifies `.claude/settings.json` post-install.** The settings-fragment merges once at install time; runtime hook execution doesn't touch config.

## Failure modes (all soft)

- **`MEMORY_VAULT_PATH` unset:** exits 0 with no output. Session proceeds without MemoryVault context.
- **Vault path doesn't exist:** stderr warning `vault path not found: <path>` + exit 0.
- **`_always-load/` directory missing:** stderr `[memory-recall-session-start] Loaded 0 MemoryVault always-load entries` + exit 0.
- **Time budget exceeded mid-load:** stderr warning naming the budget overrun + partial results emitted + exit 0.
- **Python unavailable:** the shell wrapper exits 0 silently (mirrors the graceful-skip pattern used elsewhere in the memory skill).

## Antigravity equivalent

Antigravity has no first-class hook surface (per the v0.7.0 installer reality). The equivalent pattern there is an **always-on rule** (`kind: rule`) that reads from `MemoryVault/personal-private/_always-load/` at agent boot. That rule is **not shipped in this hook directory** — it's a future companion customization tracked under MemoryVault's discovery-mining part.

## See also

- [`memory-recall-prompt-submit`](../memory-recall-prompt-submit/hook.md) — companion UserPromptSubmit hook (lands in plan #7a part 2 task 2).
- [`memory` skill](../../skills/memory/SKILL.md) — write primitives + the `recall.py` script this hook invokes.
- [MemoryVault recall-loop part](../../wiki/explanation/designs/memoryvault/parts/recall-loop.md) — full architectural context for the two-hook recall pattern.
