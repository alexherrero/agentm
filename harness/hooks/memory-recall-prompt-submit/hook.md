---
name: memory-recall-prompt-submit
description: "UserPromptSubmit hook that injects query-relevant MemoryVault entries before the agent processes a prompt. Calls the recall engine for top-K matches, dedups against already-loaded `_always-load` entries, emits a transparency line listing what got loaded. Hard time budget 300ms with degraded-graceful overrun. Plan #7a part 2 of MemoryVault Core (scaffold ships in task 2; engine wires in task 3)."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# memory-recall-prompt-submit — inject query-relevant MemoryVault entries on prompt submit

A `UserPromptSubmit` event hook that takes the user's prompt as a recall query, calls the recall engine for top-K relevant MemoryVault entries, dedups against the entries already loaded by `memory-recall-session-start`, and injects the remaining matches as additional context before the agent processes the prompt.

## How it works

- **Trigger:** Claude Code's `UserPromptSubmit` event (matcher `.*` — fires on every user prompt).
- **Vault resolution:** reads `MEMORY_VAULT_PATH` env var. If unset, exits 0 silently (no-op — the hook never breaks a session where MemoryVault isn't configured).
- **Input:** stdin JSON from Claude Code with at minimum a `prompt` field (the user's submitted text).
- **Recall query:** prompt text → recall engine → top-K relevant entries (default K=5).
- **Dedup:** entries with paths matching the always-load set (loaded by `memory-recall-session-start`) are dropped — no redundant injection.
- **Output:**
  - **stdout** — formatted markdown block: a header line + each entry's body (frontmatter stripped) separated by `---` rules. Claude Code injects this as additional context before the agent sees the prompt.
  - **stderr** — one transparency line: `[memory-recall-prompt-submit] Loaded N relevant entries: <slug-list>` (shown to operator in hook logs).
- **Time budget:** 300ms wall clock, enforced by **stream admission** — each half of the search runs to completion or does not run at all. A stream whose cost cannot fit the remaining budget is declined before it starts, and a stream cut short mid-flight has its results discarded rather than returned. The hook never blocks the user prompt.
  - **Why not "return the partial results gathered so far"** (this hook's contract until GH #92): the lexical scorer computes IDF and average document length over the candidate set its walk actually reached, so a walk stopped halfway does not yield the first half of the real ranking — it yields a *different* ranking over an arbitrary, walk-order-determined subset. Returning that as "partial results" surfaces confident-looking entries that no complete search would have chosen, and nothing downstream can tell the difference. Silence is the honest degradation here.
  - **On overrun:** stderr names which streams were blocked and why. A zero-result recall must distinguish *"searched, found nothing"* from *"could not search"* — reporting both as `Loaded 0` is how GH #92 stayed invisible under green CI for months.
- **Exit 0** always (unless a Python interpreter error — vault / engine problems are warnings, not failures).

## Implementation status (task 2 of plan #7a part 2)

This task ships the **hook scaffold** — the hook installs at its host destination + the settings-fragment merges + the shell wrappers run cleanly + stdin is parsed + the transparency line is emitted. The **recall engine** (sqlite-vec query + grep+frontmatter parallel + rank-merge) lands in task 3 of this part. Until then, the `prompt-submit` subcommand of `recall.py` returns an empty result set + a placeholder transparency line `[memory-recall-prompt-submit] (scaffold — recall engine lands in task 3)`. The hook contract is stable; only the engine logic is incremental.

## What it never does

- **Never blocks the user prompt.** If anything fails (vault missing, Python missing, recall engine errors), the hook exits 0 and the agent processes the prompt as-is.
- **Never injects duplicate context.** Always-load entries already in the session context are filtered out of the recall results by path.
- **Never reads `_archive/` or `_inbox/` by default.** `_archive/` is always excluded; `_inbox/` is excluded unless a future `--include-inbox` flag is set (deferred to manual `/memory search` in plan #7a part 3+).
- **Never surfaces `status: superseded` entries.** Filtered by default.
- **Never writes to the vault.** Pure read-only.

## Failure modes (all soft)

- **`MEMORY_VAULT_PATH` unset:** exits 0 with no output.
- **Vault path doesn't exist:** stderr warning + exit 0.
- **Stdin not valid JSON or `prompt` field missing:** stderr warning + exit 0 (graceful — the agent still gets the prompt).
- **Recall engine raises (any reason):** stderr warning + exit 0 with empty injection (degraded-graceful — agent processes prompt without memory context this turn).
- **Time budget exceeded mid-recall:** stderr warning naming the budget overrun + the blocked streams and their reasons + whatever *complete* streams produced + exit 0. Never a partially-walked ranking.
- **Vector index unreachable:** the semantic half is skipped in ~1ms and named on stderr; the lexical half gets the whole budget. Notably this is the steady state on macOS system Python, whose `sqlite3` is built without `enable_load_extension`, so `sqlite-vec` can never load — worth checking before assuming the index is merely unbuilt.
- **Python unavailable:** the shell wrapper exits 0 silently.

## Antigravity equivalent

Antigravity has no first-class hook surface (per the v0.7.0 installer reality). The equivalent pattern there is a **per-prompt skill auto-invocation** — a skill that triggers on every prompt + queries the recall engine. That skill is **not shipped in this hook directory** — it's a future companion customization tracked under MemoryVault's discovery-mining part.

## See also

- [`memory-recall-session-start`](../memory-recall-session-start/hook.md) — companion SessionStart hook that loads `_always-load/` entries.
- [`memory` skill](../../skills/memory/SKILL.md) — write primitives + the `recall.py` script this hook invokes.
- [MemoryVault recall-loop part](../../wiki/explanation/designs/memoryvault/parts/recall-loop.md) — full architectural context for the two-hook recall pattern.
