---
name: memory-recall-prompt-submit
description: "UserPromptSubmit hook that injects query-relevant MemoryVault entries before the agent processes a prompt. Calls the recall engine for top-K matches, dedups against already-loaded `_always-load` entries, emits a transparency line listing what got loaded. Hard time budget 300ms with degraded-graceful overrun. Plan #7a part 2 of MemoryVault Core (scaffold ships in task 2; engine wires in task 3)."
kind: hook
supported_hosts: [claude-code, antigravity]
version: 0.1.0
---

# memory-recall-prompt-submit — inject query-relevant MemoryVault entries on prompt submit

A `UserPromptSubmit` event hook that takes the user's prompt as a recall query, calls the recall engine for top-K relevant MemoryVault entries, dedups against the entries already loaded by `memory-recall-session-start`, and injects the remaining matches as additional context before the agent processes the prompt.

## How it works

- **Trigger:** Claude Code's `UserPromptSubmit` event (matcher `.*` — fires on every user prompt).
- **Vault resolution:** reads `MEMORY_ROOT` env var. If unset, exits 0 silently (no-op — the hook never breaks a session where MemoryVault isn't configured).
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

## Recall exclusions

A project's `project.yaml` can name other projects under `recall_excludes:`, and a session working on that project gets no hit from them. The folder comes from the payload's `cwd`, or from the hook's own folder when the payload has none, and it counts when it is under one of the project's `code_paths`, under the project's folder in the vault, or in a linked git worktree of either. The hook drops a hit that sits under `projects/<slug>/` or `projects/completed/<slug>/`, or whose `project:` names an excluded project, before the five are chosen, on both engines. The stderr line ends `(recall_excludes: <slugs>)` when the rule applied.

A rule that cannot be read fails closed: no recall runs for that prompt, and stderr says `recall exclusions could not be read`, with the reason. See [Vault project.yaml reference](../../../wiki/reference/Vault-Project-Yaml.md#recall-excludes).

## What it never does

- **Never blocks the user prompt.** If anything fails (vault missing, Python missing, recall engine errors), the hook exits 0 and the agent processes the prompt as-is.
- **Never injects duplicate context.** Always-load entries already in the session context are filtered out of the recall results by path.
- **Never reads `_archive/` or `_inbox/` by default.** `_archive/` is always excluded; `_inbox/` is excluded unless a future `--include-inbox` flag is set (deferred to manual `/memory search` in plan #7a part 3+).
- **Never surfaces `status: superseded` entries.** Filtered by default.
- **Never writes to the vault.** Pure read-only.

## Failure modes (all soft)

- **`MEMORY_ROOT` unset:** exits 0 with no output.
- **Vault path doesn't exist:** stderr warning + exit 0.
- **Stdin not valid JSON or `prompt` field missing:** stderr warning + exit 0 (graceful — the agent still gets the prompt).
- **Recall engine raises (any reason):** stderr warning + exit 0 with empty injection (degraded-graceful — agent processes prompt without memory context this turn).
- **Time budget exceeded mid-recall:** stderr warning naming the budget overrun + the blocked streams and their reasons + whatever *complete* streams produced + exit 0. Never a partially-walked ranking.
- **`recall_excludes` cannot be read:** stderr names the reason + exit 0 with empty injection. This is the one failure that withholds what a healthy search would have found, because the session cannot be told apart from one the rule covers.
- **Vector index unreachable:** the semantic half is skipped in ~1ms and named on stderr; the lexical half gets the whole budget. Notably this is the steady state on macOS system Python, whose `sqlite3` is built without `enable_load_extension`, so `sqlite-vec` can never load — worth checking before assuming the index is merely unbuilt.
- **Python unavailable:** the shell wrapper exits 0 silently.

## Antigravity equivalent

agy has no `UserPromptSubmit`; the nearest event is `PreInvocation`, which fires before every model call, not once per message. It is registered on `agy` 1.4 by the `agentm` plugin that `install.sh` builds from the installed hooks (`scripts/emit_agy_hooks.py`; this file's `supported_hosts` is what puts it there), gated like the MCP step: `~/.gemini/` exists and `agy` is on the path. The handler runs this same script through the vendored adapter, which maps `lastUserInput` to `prompt` and lets the hook through once per user message (it remembers a hash of the message per conversation and answers `{}` for the repeats; a message identical to the one before it is also skipped). The recall arrives as an `ephemeralMessage`.

**What that costs, measured on `agy` 1.4.12.** An `ephemeralMessage` is shown to the model for one model call, not for the whole user message. In a live run the prompt asked the model to run a shell command and then answer from the recalled entry: `PreInvocation` fired twice, the recall was stored as an `EPHEMERAL_MESSAGE` step before the first call, and the model's answer after the tool call did not have it. So recall reaches the model's first decision on each message, which is where it plans, but not a later step that comes back to it after a tool call. A plain question with no tool call gets all of it. Claude Code keeps the injected text in the conversation, so this is a real difference. A persistent `systemMessage` would match Claude Code, at the price of adding every message's recall to the context for the rest of the session; that needs an adapter option, which belongs upstream in crickets, so it is not done here. The agent can still call `memory_search` for anything it needs later.

The same `RECALL_TOKEN_BUDGET=10000`, `AGENTM_HOST` and `AGENTM_AGY_RECALL=off` apply as for [`memory-recall-session-start`](../memory-recall-session-start/hook.md#antigravity-equivalent).

## See also

- [`memory-recall-session-start`](../memory-recall-session-start/hook.md) — companion SessionStart hook that loads `_always-load/` entries.
- [`memory` skill](../../skills/memory/SKILL.md) — write primitives + the `recall.py` script this hook invokes.
- [MemoryVault recall-loop part](../../wiki/explanation/designs/memoryvault/parts/recall-loop.md) — full architectural context for the two-hook recall pattern.
