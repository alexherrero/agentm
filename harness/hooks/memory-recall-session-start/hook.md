---
name: memory-recall-session-start
description: SessionStart hook that loads `MemoryVault/personal-private/_always-load/*.md` entries into session context. Fires once per session boot; emits "Loaded N MemoryVault always-load entries" transparency line. Hard time budget 500ms with degraded-graceful overrun. Plan #7a part 2 of MemoryVault Core.
kind: hook
supported_hosts: [claude-code, antigravity]
version: 0.1.0
---

# memory-recall-session-start — load always-load MemoryVault entries on session boot

A `SessionStart` event hook that globs `MemoryVault/personal-private/_always-load/*.md`, reads each entry, and injects the bodies into the session as additional context. The agent sees the always-load entries before processing any user input.

## How it works

- **Trigger:** Claude Code's `SessionStart` event (matcher `.*` — fires on startup, resume, clear, and compact).
- **Vault resolution:** reads `MEMORY_ROOT` env var. If unset, exits 0 silently (no-op — the hook never breaks a session where MemoryVault isn't configured).
- **Glob:** `<vault>/personal-private/_always-load/*.md`.
- **Filter:** entries with `status: superseded` in their frontmatter are skipped (defense-in-depth; supersession normally moves entries to `_archive/` but we filter here too).
- **Output:**
  - **stdout** — formatted markdown block: a header line + each entry's body (frontmatter stripped) separated by `---` rules. Claude Code injects this as additional session context.
  - **stderr** — one transparency line: `[memory-recall-session-start] Loaded N MemoryVault always-load entries (B bytes injected): <slug-list>` (shown to operator in hook logs but not the agent's context). The tier is `standards/` at the vault root — `storage-rules.md`, `user-preferences.md`, `security-and-secret-governance.md`; generated `moc-*` files and the `voice/` library are never injected — with the retired pen `memory/_always-load/` read behind it while it still holds anything.
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
- **Written for Claude Code only.** The hook skips the marker (and the briefing below) when `AGENTM_HOST` is set to anything but `claude-code`, or when `transcript_path` is under a `.gemini` directory: the Antigravity registration sets the variable, and an agy transcript is not in the format the idle job reads. See [Antigravity equivalent](#antigravity-equivalent).

## Recall exclusions

The hook hands `recall.py session-start --stdin-payload` the event's JSON, so a project's `recall_excludes:` rule follows the session's folder and not this process's (see [`memory-recall-prompt-submit`](../memory-recall-prompt-submit/hook.md#recall-exclusions)). The tier is the operator's standing rules and carries no project, so the rule can only ever remove a pen entry stamped with an excluded project. If the rule cannot be read, every entry that carries a project is withheld, the standing rules still load, and stderr says `recall exclusions could not be read`.

## What it never does

- **Never blocks session start.** If anything fails (vault missing, Python missing, entries unreadable), the hook exits 0 and the session proceeds without injected memory.
- **Never reads outside `<vault>/personal-private/_always-load/`.** Group-scoped `_always-load/` directories (e.g. `<vault>/work-public/_always-load/`) are reserved for future per-group recall; v0.1.0 hardwires `personal-private/`.
- **Never writes to the vault.** Pure read-only.
- **Never modifies `.claude/settings.json` post-install.** The settings-fragment merges once at install time; runtime hook execution doesn't touch config.

## Failure modes (all soft)

- **`MEMORY_ROOT` unset:** exits 0 with no output. Session proceeds without MemoryVault context.
- **Vault path doesn't exist:** stderr warning `vault path not found: <path>` + exit 0.
- **`_always-load/` directory missing:** stderr `[memory-recall-session-start] Loaded 0 MemoryVault always-load entries` + exit 0.
- **Time budget exceeded mid-load:** stderr warning naming the budget overrun + partial results emitted + exit 0.
- **Python unavailable:** the shell wrapper exits 0 silently (mirrors the graceful-skip pattern used elsewhere in the memory skill).

## Antigravity equivalent

It is registered on `agy` 1.4 by the `agentm` plugin that `install.sh` builds from the installed hooks (`scripts/emit_agy_hooks.py`; this file's `supported_hosts` is what puts it there), gated like the MCP step: `~/.gemini/` exists and `agy` is on the path. The handler runs this same script through the adapter vendored at `adapters/antigravity/hooks/`, which rebuilds Claude's payload from agy's (`session_id` from `conversationId`, `cwd` from the first of `workspacePaths`, `transcript_path` from `transcriptPath`) and hands the script's stdout to agy as a `systemMessage`, which stays in the conversation. The handler sets two things in the script's environment: `AGENTM_HOST=antigravity` and `RECALL_TOKEN_BUDGET=10000` (a value already in the environment wins).

Three things this hook does for Claude Code it does not do there:

- **It writes no crash-recovery marker.** The marker names the host's transcript, and [`memory-reflect-idle`](../memory-reflect-idle/hook.md) later reflects on that file in Claude Code's JSONL with `--route` aimed at the real vault. An agy transcript (`transcript_full.jsonl`, rows of `step_index` / `source` / `type` / `status` / `content`) is a different format, so a marker written for one would hand the idle job a file it cannot read. The three ways out were to skip the marker off Claude, to teach the reflection side agy's format, or to write the marker and hope; this takes the first. The second is real work with nothing yet measuring what reflection makes of an agy transcript, so until it is done, nothing awaits reflection on agy: the idle job has no marker to find, so there is nothing for it to mis-read. The hook skips the marker when `AGENTM_HOST` is anything but `claude-code` (unset, as in every Claude Code registration, counts as Claude Code), and also when the payload's `transcript_path` is under a `.gemini` directory, which no Claude transcript is. Either signal alone is enough, so a registration that lost the variable still cannot leave a marker. `scripts/test_agy_recall_hooks.py` shows an agy session start leaves none, that the idle job then hands `reflect.py` nothing, and, as a control, that with both signals removed the same payload does leave a marker and the idle job does hand over the agy file.
- **It does not look for scripts in the opened workspace.** On Claude Code the hook finds `recall.py` and the briefing under `.claude/skills/memory/scripts/` in the project first (the project-scope install). On agy the working directory is whatever folder was opened, and agy runs a plugin's hooks without asking whether to trust that folder, so a cloned repository shipping its own `recall.py` would be executed at every session start. With `AGENTM_HOST` set to anything but `claude-code` the lookup goes straight to the install prefix and the recorded source clone. `memory-recall-prompt-submit` does the same.
- **It does not run the pending-state briefing.** The briefing records that it was shown, so a session that only injects it into a model (an `agy -p` run nobody reads) would use up the one showing the operator is owed. Claude Code still gets it.

**Size.** agy cuts one injected message at 49,309 characters whatever it holds, keeps the head and replaces the rest with `<truncated N bytes>` and a note (measured on `agy` 1.4.12 with a 72 KB payload of English text and again of hex text: the stored message was the same length both times, so it is a character cap, not a token cap). The recall engine's default budget (20,000 estimated tokens, about 80 KB) would hand agy a message it then cuts mid-entry, so the handler sets `RECALL_TOKEN_BUDGET=10000` (about 40 KB): over that, the engine drops whole entries by its own priority order and names them in a note the model reads. Today's always-load set is about 25 KB, so nothing is dropped yet.

**Turning it off.** `AGENTM_AGY_RECALL=off` in the environment of an `agy` run makes every agentm handler answer `{}` without running a hook (checked live: the variable set on `agy -p` reaches the hook process). `agy plugin disable agentm` turns the plugin off for good, and a later `install.sh` keeps it disabled. A script that shells out to `agy -p` and does not want the vault in it, such as a cross-model review, should set the variable.

## See also

- [`memory-recall-prompt-submit`](../memory-recall-prompt-submit/hook.md) — companion UserPromptSubmit hook (lands in plan #7a part 2 task 2).
- [`memory` skill](../../skills/memory/SKILL.md) — write primitives + the `recall.py` script this hook invokes.
- [MemoryVault recall-loop part](../../wiki/explanation/designs/memoryvault/parts/recall-loop.md) — full architectural context for the two-hook recall pattern.
