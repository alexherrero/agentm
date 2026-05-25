# AGENTS.md

Universal instructions for AI coding agents working in a project using `agentm`. Antigravity, Gemini CLI, Cursor, and other tools that read `AGENTS.md` should use this as the entry point. Claude Code users should also read this file (it's linked from `CLAUDE.md`).

## What this harness is

A phase-gated workflow with on-disk state. You are expected to execute exactly one phase per session, not freestyle across the full development lifecycle in one go.

## Phases (hard boundaries)

1. **Setup** — first-time scaffold, feature list, `init.sh`. Run once per project.
2. **Plan** — turn a brief into `.harness/PLAN.md` with tasks and verification criteria. No code written.
3. **Work** — pick one task from the plan, implement it, update `progress.md`. Stop.
4. **Review** — adversarial critique. Assume the code has bugs. Produce a failing test or a specific line-number defect — not prose.
5. **Release** — pre-merge gate. Clean tree, all verification passes, changelog updated.
6. **Bugfix** — a different pipeline: Report → Analyze → Fix → Verify. Used instead of Plan+Work for bugs.

Each phase has a canonical spec in [`harness/phases/`](harness/phases/). Tool-specific adapters (slash commands for Claude Code, equivalent entrypoints for Antigravity) point back to those canonical specs.

## Non-negotiable rules

1. **Read `.harness/PLAN.md` before doing anything in `/work` or `/review`.** If it doesn't exist, you're in the wrong phase — stop and run `/plan` first.
2. **Single task per `/work` session.** Do not implement multiple tasks "while you're in there." The plan exists to sequence work; respect the sequence.
3. **Verification must be executable.** LLM-judge "looks good to me" is not verification. Deterministic checks (typecheck, lint, tests, build) come first; LLM review augments, never replaces.
4. **State is on disk, not in this conversation.** Write progress to `.harness/progress.md` at the end of every phase. The next session won't have your context.
5. **Do not delete or edit tests to make them pass.** If a test is wrong, surface it and stop for human input.
6. **Sub-agents are for read-only fan-out**, not parallel implementation. Dispatch them to gather context; never to edit code.

## Conventions

### Commit messages

Do not append a `Co-Authored-By:` trailer naming the agent or model (`Co-Authored-By: Claude`, `Co-Authored-By: Gemini`, etc.) to git commit messages. The user is the sole author of intent — the agent is the tool, not a co-author. Plain commit message only. Applies to every commit unless the user explicitly opts in for a specific commit.

This applies regardless of which host you're running in (Claude Code, Antigravity, Gemini CLI) and regardless of any default the host injects. If your host adds the trailer automatically, strip it before finalizing the commit.

## Directory layout (in a project that installs this harness)

```
your-project/
├── .harness/
│   ├── PLAN.md             # current plan — goal, tasks, verification criteria
│   ├── features.json       # structured feature list with passes: true|false
│   ├── progress.md         # append-only log of what was done, when, what's next
│   ├── init.sh             # one-shot script to boot the dev environment
│   ├── known-migrations.md # per-project recipes for dependabot-fixer skill
│   └── scripts/            # shell helpers — cross-review.{sh,ps1} (Gemini shell-out), etc.
├── AGENTS.md               # this file (or a pointer to it)
├── CLAUDE.md               # Claude Code entry point — points back here
└── .claude/
    ├── commands/           # slash commands (Claude Code)
    ├── agents/             # sub-agents (Claude Code) — adversarial-reviewer, adversarial-reviewer-cross, explorer
    └── skills/             # auto-triggered skills (Claude Code) — e.g. dependabot-fixer
```

## How to invoke phases

- **Claude Code:** `/plan <brief>`, `/work`, `/review`, `/release`, `/bugfix <report>`.
- **Antigravity / tools without slash commands:** prompt the agent with "Run the plan phase per AGENTS.md" (or work / review / etc.). The agent should read [`harness/phases/`](harness/phases/) and follow the spec.

## Personal customizations

Skills, sub-agents, hooks, MCP servers, slash commands, bundles, etc. live in the sibling [`crickets`](https://github.com/alexherrero/crickets) repo (since v2.0.0 / [ADR 0006](wiki/explanation/decisions/0006-crickets-split.md)). Install both repos as siblings (e.g. `~/Antigravity/agentm/`, `~/Antigravity/crickets/`) to get the full set. The harness's `/release` and `/work` phases reference `ship-release` (from crickets) as a graceful-skip suggestion — present in toolkit, suggested by harness, neither requires the other to exist.

## Core principles (why the harness looks like this)

See [harness/principles.md](harness/principles.md) for the full reasoning. Short version:

- Context is ephemeral, files are durable.
- Coherence-critical work (coding) should be single-threaded; read-only breadth can fan out.
- Deterministic verification is cheap and truthful; LLM judgment is expensive and sycophantic.
- Adversarial review only finds bugs if the reviewer is primed to assume bugs exist.
- Re-audit the harness whenever the underlying model ships a new version — scaffolding decays.
