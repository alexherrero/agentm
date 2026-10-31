# Antigravity adapter

Adapter for running agentm in [Antigravity](https://antigravity.google.com/). Since the V5 unbundling this adapter is **slim**: it ships the always-on operating-contract rules plus the shared utility skills. The phase-gated dev loop (setup/plan/work/review/release/bugfix) and the review sub-agents are no longer vendored by agentm — they're provided by the crickets development-lifecycle / code-review plugins, which install their own Antigravity surface.

## What this adapter ships

| Claude Code | Antigravity | Purpose |
|---|---|---|
| `CLAUDE.md` pointer | `.agents/rules/harness.md` (`trigger: always_on`) | Always-on operating contract |
| — | `.agents/rules/agentmemory-context.md` (`trigger: always_on`) | AgentMemory vault context |
| `.claude/skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | Shared utility skill (doctor) |
| — | `agy mcp add agentmemory …` (user level, by `install.sh`) | The `memory_search` and `memory_capture` tools |
| `UserPromptSubmit` / `SessionStart` hooks in `settings.json` | an `agy` plugin named `agentm` (`agy plugin install`, by `install.sh`) | The session-start brief and the per-message recall |

The MCP registration happens only when `~/.gemini/` exists, `agy` is on the path and the memory daemon answers `/health` (the daemon is macOS-only, so `install.ps1` has no counterpart). It uses the daemon's own `daemon.port`.

The shared skill is delivered to `.agents/skills/` by `install.sh` / `install.ps1` (sourced from `adapters/claude-code/skills/`, the parity-enforced single copy). Antigravity reads that path natively per the Agent Skills standard.

## Why the dev loop isn't here (V5 unbundling)

agentm's repositioning in V5 is "storage-agnostic memory OS + plugin host". The phase-gated dev loop that used to live in `adapters/antigravity/workflows/` + `adapters/antigravity/skills/` (the `explorer` / `adversarial-reviewer` / `adversarial-reviewer-cross` review sub-agents) moved to the crickets **development-lifecycle** and **code-review** plugins. A bare agentm install is intentionally unaware of that dev loop: it's optional, provided by crickets when installed, with no pointer and no requirement from agentm's side.

If you want the full plan/work/review/release loop in Antigravity, install the crickets developer plugins alongside agentm. `agy` plugins have no commands folder, so crickets ships each phase as a skill. A bare `/plan` is agy's own planning mode; type `/development-lifecycle:plan` for the crickets one.

## Automatic recall on Antigravity

Claude Code gets the vault's session-start brief and a recall before each prompt from agentm's hooks. `agy` 1.4 has a file-based hook surface (a `hooks.json` at a plugin's root, six events, answers as JSON on stdout), so `install.sh` registers three of those hooks there too, as a small plugin named `agentm`: `memory-recall-session-start` and `project-brief-session-start` on `SessionStart`, and `memory-recall-prompt-submit` on `PreInvocation`. A hook is registered when its `hook.md` lists `antigravity` in `supported_hosts`; [`scripts/emit_agy_hooks.py`](../../scripts/emit_agy_hooks.py) builds the plugin from the installed hooks' settings fragments and `agy plugin install` puts it in place. It runs under the same gate as the MCP step (`~/.gemini/` exists and `agy` is on the path), and needs no daemon.

Each handler runs the *installed* hook script, the one Claude Code runs, through [`hooks/_agy_hook_adapter.py`](hooks/_agy_hook_adapter.py). That adapter is crickets' (`scripts/agy_hook_adapter.py`), copied byte for byte so agentm needs no crickets on the machine; [`hooks/UPSTREAM`](hooks/UPSTREAM) records the commit and sha256 it came from, and the `agy-adapter` mode of `scripts/check-vendored-parity.sh` fails if the copy is edited. It rebuilds Claude's stdin payload from agy's, runs the script from the workspace root, and turns the script's output into agy's JSON.

What differs from Claude Code, each measured on `agy` 1.4.12:

- **No crash-recovery marker, no pending-state briefing.** The session-start hook writes a marker naming the host's transcript, which the idle job later reflects on in Claude Code's format with `--route` aimed at the real vault; an agy transcript is a different JSONL. The hook skips the marker off Claude Code (the registration sets `AGENTM_HOST=antigravity`; a transcript under `.gemini` is skipped too). It skips the briefing for the same reason: the briefing records that it was shown, so a run nobody reads would use up the operator's showing. Reflection stays Claude Code's: `memory-reflect-stop` and `memory-reflect-idle` are not registered on agy.
- **A workspace cannot supply the hook scripts.** On Claude Code the hooks look for `.claude/skills/memory/scripts/` under the project first. agy runs a plugin's hooks without asking whether to trust the folder that was opened, so off Claude Code the hooks skip that lookup and use the install prefix and the recorded source clone only.
- **One injected message is cut at 49,309 characters.** The handlers set `RECALL_TOKEN_BUDGET=10000` (about 40 KB), so the recall engine drops whole entries and names them rather than agy cutting one mid-sentence.
- **Per-message recall lasts one model call.** agy has no `UserPromptSubmit`; the recall rides on `PreInvocation` as an `ephemeralMessage`, which the model sees on the first call after a message and not after a tool call. The adapter lets the hook through once per message.
- **It can be turned off.** `AGENTM_AGY_RECALL=off` in an `agy` run's environment silences every handler for that run (set it in a script that shells out to `agy -p`, such as a cross-model review); `agy plugin disable agentm` turns the plugin off, and a later `install.sh` leaves it disabled.

The PowerShell installer has no counterpart: agy runs a hook through `sh -c`.

## Layout

```
adapters/antigravity/
├── README.md                             (this file)
├── hooks/
│   ├── _agy_hook_adapter.py              (crickets' hook adapter, vendored byte for byte)
│   └── UPSTREAM                          (the crickets commit and sha256 it was taken from)
└── rules/
    ├── harness.md                        (trigger: always_on — operating contract)
    └── agentmemory-context.md            (trigger: always_on — AgentMemory vault context)
```

`install.sh` (POSIX) or `install.ps1` (Windows/PowerShell 7+) copies the rules to the target's `.agents/rules/` and delivers the shared skills to `.agents/skills/`, with the same managed-file semantics as the Claude Code adapter: refreshed on `--update` / `-Update`, preserved on fresh install if already present.

## Single source of truth

The shared skills here point back to their canonical specs under [`harness/skills/`](../../harness/skills/). If an adapter copy drifts from the canonical spec, the canonical spec wins — file an issue or fix it. `scripts/check-parity.sh` pins the canonical set.
