# AGENTS.md

Universal instructions for AI coding agents working in a project using `agentm`. Antigravity, Gemini CLI, Cursor, and other tools that read `AGENTS.md` should use this as the entry point. Claude Code users should also read this file (it's linked from `CLAUDE.md`).

## What this harness is

A phase-gated workflow with on-disk state — single-threaded, one phase per session, with context carried between sessions on disk rather than in the conversation. Since the V5 unbundling ([ADR 0011](wiki/decisions/0011-v5-unbundling-dev-loop.md)) the phase loop itself (Setup · Plan · Work · Review · Release · Bugfix) is provided by the companion crickets **developer-workflows** / **code-review** plugins; `agentm` owns the durable state substrate and the memory engine those phases run on. The plugins are optional — a bare `agentm` is the memory engine alone.

## Phases (hard boundaries)

1. **Setup** — first-time scaffold, feature list, `init.sh`. Run once per project.
2. **Plan** — turn a brief into `.harness/PLAN.md` with tasks and verification criteria. No code written.
3. **Work** — pick one task from the plan, implement it, update `progress.md`. Stop.
4. **Review** — adversarial critique. Assume the code has bugs. Produce a failing test or a specific line-number defect — not prose.
5. **Release** — pre-merge gate. Clean tree, all verification passes, changelog updated.
6. **Bugfix** — a different pipeline: Report → Analyze → Fix → Verify. Used instead of Plan+Work for bugs.

Each phase's canonical spec ships in the crickets **developer-workflows** plugin (Claude Code slash commands, Antigravity entrypoints) — `agentm` no longer vendors them, having retired the byte-duplicated copies in the V5 unbundling.

## Non-negotiable rules

1. **Read `.harness/PLAN.md` before doing anything in `/work` or `/review`.** If it doesn't exist, you're in the wrong phase — stop and run `/plan` first.
2. **Assume the full task list; safety-gate each task.** A `/work` session works the plan's tasks autonomously, in sequence — no per-task approval. Before each task, run a safety pre-check and **stop to ask only when it fails (hard-to-reverse / ambiguous / scope-drifting / unverifiable) or a clarification is needed**; otherwise run to the end of the plan. Single-threaded always — never fan out parallel implementers.
3. **Verification must be executable.** LLM-judge "looks good to me" is not verification. Deterministic checks (typecheck, lint, tests, build) come first; LLM review augments, never replaces.
4. **State is on disk, not in this conversation.** Write progress to `.harness/progress.md` at the end of every phase. The next session won't have your context.
5. **Tests check real behavior. Don't dumb them down, disable them, or convert them into change-detectors.** A failing test is information — read it, understand the behavior it asserts, and fix the implementation. The forbidden moves are: weakening an assertion so it tolerates the broken output ("dumbing down"); slapping `@skip` / `xfail` on a real failure ("disabling"); or rewriting an assertion to match whatever the code now produces without preserving what's being checked ("change-detector"). When an accepted code change genuinely extends or alters the contract a test exercises, **updating the test is required** — not optional, not "stop for input" — to keep it checking the same semantic intent against the new contract. The distinction is *why* you're editing: to keep verifying the same behavior under a changed shape (allowed), or to make a real failure go away (forbidden).
6. **Sub-agents are for read-only fan-out**, not parallel implementation. Dispatch them to gather context; never to edit code.

## Conventions

### Commit messages

Do not append a `Co-Authored-By:` trailer naming the agent or model (`Co-Authored-By: Claude`, `Co-Authored-By: Gemini`, etc.) to git commit messages. The user is the sole author of intent — the agent is the tool, not a co-author. Plain commit message only. Applies to every commit unless the user explicitly opts in for a specific commit.

This applies regardless of which host you're running in (Claude Code, Antigravity, Gemini CLI) and regardless of any default the host injects. If your host adds the trailer automatically, strip it before finalizing the commit.

### Running the checks (the standard gate battery)

Before every commit, run the full local gate battery — it's the one command that mirrors CI's deterministic checks plus the V4 push-surface integration test:

```bash
bash scripts/check-all.sh
```

It runs the unit suite (`scripts/test_*.py`) + every `check-*` gate (syntax · references · adapters · parity · lib-parity · no-pii · wiki) + `verify-v4.sh` (the auto-orchestration push-surface integration check, against a throwaway scratch vault), prints a PASS/FAIL table, and exits non-zero if any gate fails. CI additionally runs the heavier `smoke-install` + `gitleaks` on every push. As the project grows, add new checks to `check-all.sh` (and a CI step) so the battery stays the single source of truth for "is it green." Full reference: [wiki/reference/CI-Gates.md](wiki/reference/CI-Gates.md).

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
    ├── commands/           # slash commands (Claude Code) — phase commands come from crickets developer-workflows
    ├── agents/             # sub-agents (Claude Code) — memory-engine: adapt-evaluator, memory-idea-researcher (review agents come from crickets)
    └── skills/             # auto-triggered skills (Claude Code) — e.g. doctor (dev/docs skills come from crickets)
```

## How to invoke phases

These entrypoints are provided by the crickets **developer-workflows** plugin — a bare `agentm` does not ship them:

- **Claude Code:** `/plan <brief>`, `/work`, `/review`, `/release`, `/bugfix <report>`.
- **Antigravity / tools without slash commands:** prompt the agent with "Run the plan phase" (or work / review / etc.); the agent reads the plugin's phase spec and follows it.

## Personal customizations

Skills, sub-agents, hooks, MCP servers, slash commands, bundles, etc. live in the sibling [`crickets`](https://github.com/alexherrero/crickets) repo (since v2.0.0 / [ADR 0006](wiki/decisions/0006-crickets-split.md)); since the V5 unbundling ([ADR 0011](wiki/decisions/0011-v5-unbundling-dev-loop.md)) the phase loop lives there too. Install both repos as siblings (e.g. `~/Antigravity/agentm/`, `~/Antigravity/crickets/`) to get the full set. The developer-workflows `/release` and `/work` phases reference `ship-release` (also from crickets) as a graceful-skip suggestion — neither requires the other to exist.

## Core principles (why the harness looks like this)

See [harness/principles.md](harness/principles.md) for the full reasoning. Short version:

- Context is ephemeral, files are durable.
- Coherence-critical work (coding) should be single-threaded; read-only breadth can fan out.
- Deterministic verification is cheap and truthful; LLM judgment is expensive and sycophantic.
- Adversarial review only finds bugs if the reviewer is primed to assume bugs exist.
- Re-audit the harness whenever the underlying model ships a new version — scaffolding decays.
