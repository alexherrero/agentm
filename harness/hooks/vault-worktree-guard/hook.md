---
name: vault-worktree-guard
description: "PreToolUse hook, registered once machine-wide, that refuses a tool call which would create a git worktree in the vault: a background-task chip whose session would start there, EnterWorktree, a subagent with worktree isolation, or a Bash command that adds a worktree. Keys strictly on the vault, resolved through harness_memory.vault_path(): its repository by git common dir and its root by resolved path. Worktrees anywhere else, agentm's and crickets' included, pass untouched. Fails open."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# vault-worktree-guard — keep git worktrees out of the vault

A git worktree doesn't belong in the vault, and this hook refuses the tool calls that would make one there.

## Why the vault

Two incidents set the rule.

- **On 2026-10-02 a worktree appeared at `projects/pixelton/.claude/worktrees/laughing-bassi-06d858`.** Google Drive mirrors the vault folder in place, so it uploaded the worktree file by file (6,276 events) and then trashed it.
- **That worktree also left `extensions.worktreeConfig = true` in the vault repository's config.** The desktop app sets the key whenever it makes a worktree. The daemon's git library refuses the extension, so on its next restart the daemon came up degraded. It made no commits, undo stopped working, and the corpus-write gate refused writes (issue #859).

There is also a rule about authority. A worktree needs operator authority, and agentm and crickets grant it through `isolation.mode: worktree-per-plan` in `.harness/project.json`. The vault has no such opt-in. This hook turns that convention into a refusal.

## What it refuses

Task 189's `routes.md` lists every route by which a worktree can reach the vault, with the evidence for each. The hook covers the four that are tool calls:

| Tool call | Refused when |
|---|---|
| `mcp__ccd_session__spawn_task`, a background-task chip | The chip's cwd (its `cwd` argument, or the session's) is in the vault. Clicking a chip starts a session in a fresh worktree under that cwd. The app makes the worktree itself, where no hook sees it, so the spawn is the last point a hook can reach. This was the 2026-10-02 route. |
| `EnterWorktree` | The cwd, or the `path` being switched into, is in the vault. This was the 2026-08-16 route, from an agentm session whose cwd had moved into the vault. |
| `Agent` (or `Task`) with `isolation: "worktree"` | The cwd is in the vault. |
| `Bash` | The command adds a worktree whose path is under the vault root, or adds one to the vault's repository. The command line is read through `cd`, `-C`, `--git-dir`, `GIT_DIR=` and `bash -c`. Comments and here-document bodies are skipped, because they are text, not commands. |

"In the vault" means one of two things. Either the path is under the vault root, or the git repository there is the vault's own. The second test catches a worktree of the vault repository wherever it would sit. A worktree of any other repository placed under the vault root is refused too, because Drive would upload it either way.

The refusal is exit 2, with a reason on stderr that Claude reads. The reason names the path, says why, and suggests what to do instead.

## What it lets through

Everything else, and in particular every worktree outside the vault. agentm and crickets use worktrees by design, and a guard keyed on worktrees in general would break their `/work` flow.

The vault is located by `harness_memory.vault_path()`, so the hook holds no path of its own and follows `$MEMORY_ROOT` like every other reader. The repository is identified by its git common dir, which is the same for a repository and every worktree of it. Both sides are compared after resolving symlinks and as paths, never as strings. A directory that only looks like the vault, such as a sibling named `<vault>-copy`, is therefore not the vault.

## What it can't reach

- **A new desktop session opened on a vault folder with the worktree option on.** The app creates the worktree before the session exists, and no hook fires.
- **`claude --worktree`, background sessions, a subagent whose definition sets `isolation: worktree`, and a scheduled task with its worktree toggle on.** None of these is a tool call the hook sees.
- **A Bash command that builds the path from a variable the hook can't expand, or a script that runs git itself.**

The `WorktreeCreate` hook event would catch some of these, but it replaces worktree creation for every repository and has no matcher, so it isn't used. For everything above, detection is the protection: the doctor's `vault-worktrees` row names a worktree in the vault, or the config key it leaves, the same day.

## It fails open

A payload it can't read, a vault that doesn't resolve, git missing from `PATH`, or the guard's own error all mean "allow". The hook fires on every Bash call on the machine, and a guard that blocked them all on its own failure would cost far more than the worktree it exists to stop.

The same reasoning sets the pre-filter in `vault-worktree-guard.sh`. A payload that mentions neither a worktree nor a chip exits before any Python starts, so an ordinary Bash call costs one string match.

## Dependencies

`python3` and `git`. `vault_worktree_guard.py` uses only the standard library, plus `harness_memory` from the agentm clone. It finds the clone from its own resolved path first, then from `source_clones.agentm` in the install config.
