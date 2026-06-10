# Product Intent

What agentm is, who it's for, and the shape of the problem it's trying to solve. Written to answer "why does this repo exist" in under five minutes — deeper reasoning lives in [`harness/principles.md`](https://github.com/alexherrero/agentm/blob/main/harness/principles.md) and the ADRs.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What is it? | A phase-gated workflow + state layout that any AI coding agent (Claude Code, Antigravity, Gemini CLI) can follow. |
| Who's it for? | A developer who wants agents to do production-quality engineering, not a demo. |
| How big is it? | Six phase commands, two sub-agents, one skill (`ship-release`). Small on purpose. |
| How does it get into a project? | One command — `install.sh /path/to/project`. See [Install-Into-Project](Install-Into-Project). |
| Why this shape? | See the six principles below, or [`harness/principles.md`](https://github.com/alexherrero/agentm/blob/main/harness/principles.md). |

## The problem

A modern coding agent can produce a working feature in a single long conversation. What it usually *can't* do in a single long conversation:

- Stop at the right time (before scope creeps).
- Run deterministic gates before declaring success.
- Leave a successor enough state to pick up cleanly.
- Review its own work adversarially instead of rubber-stamping.
- Keep the docs honest once the code ships.

These are all scaffolding problems, not model problems. They don't get better with a bigger model; they get better with better scaffolding. agentm is that scaffolding.

## The shape

Six phases with hard boundaries. Each phase has one job.

| Phase | Job | Ends with |
|---|---|---|
| `/setup` | First-time scaffold, feature list, `init.sh`. | Directory layout + `.harness/PLAN.md` stub. |
| `/plan` | Turn a brief into tasks with verification criteria. No code. | `.harness/PLAN.md` with tasks. |
| `/work` | Work the plan's task list autonomously — safety-gate each task, gates green + commit per task; stop to ask only on a failed check or needed clarification. | Tasks `[x]`; `progress.md` lines appended. |
| `/review` | Adversarial critique — must produce a failing test or line-number defect. | Executable artifact, not prose. |
| `/release` | Pre-merge gate: clean tree, full test suite, feature flags flipped truthfully. | Verified-ready state; no push. |
| `/bugfix` | Report → Analyze → Fix → Verify pipeline for bugs (replaces `/plan` + `/work`). | Fix committed with regression test. |

A separate skill, [`ship-release`](https://github.com/alexherrero/agentm/blob/main/harness/skills/ship-release.md), cuts a tagged GitHub release *after* `/release` passes — semver sized from conventional-commit prefixes in the range since the last tag.

## Target user

Someone who:

- Pays per-token and per-minute — values minimal ceremony, doesn't want a 150-agent supermarket.
- Wants the *same* workflow across Claude Code, Antigravity, and Gemini CLI so their muscle memory travels.
- Thinks tests and typecheckers are the truth and LLM reviews are augmentation.
- Is willing to invest five minutes at the end of each feature to keep the docs honest, in exchange for not having to re-derive the system later.

## The six principles (short form)

Full text: [`harness/principles.md`](https://github.com/alexherrero/agentm/blob/main/harness/principles.md).

1. **Phase-gated workflow over free-form conversation.** Each session does one thing. Fresh context at boundaries beats compaction.
2. **State lives on disk, not in context.** `.harness/PLAN.md`, `features.json`, `progress.md`. Next session starts by reading.
3. **Single-threaded for coherence; fan-out only for read-only breadth.** Parallel implementers produce inconsistent decisions. Parallel readers are fine.
4. **Deterministic verification before LLM judgment.** Typecheck → lint → test → build, *then* optional critic. A review that skips gates is not a review.
5. **Adversarial review with "assume bugs" framing.** The reviewer must produce an executable artifact, not prose. Neutral reviewers rubber-stamp.
6. **Re-audit the harness on every model bump.** Scaffolding that was load-bearing last quarter often isn't anymore.

## Non-goals

- **A one-shot "build me a feature" agent.** The harness refuses to do plan+implement+review in a single session.
- **A supermarket of agents.** Two sub-agents (`explorer`, `adversarial-reviewer`) + one skill (`ship-release`). Adding more costs coherence.
- **Replacing tests or code review.** Deterministic gates come first; LLMs augment.
- **Dynamic-doc generation from code.** Docs are human-edited narrative, updated by the `documenter` sub-agent at phase boundaries only. See [ADR 0002](0002-documentation-convention).
- **Universality across every tool.** The harness targets tools that read `AGENTS.md`. Tools without an adapter file tree need one written per [Repo-Layout](Repo-Layout).

## Related

- [How-The-Pieces-Fit](How-The-Pieces-Fit) — narrative of how phases, adapters, templates, and scripts interact.
- [Repo-Layout](Repo-Layout) — on-disk map of the four-adapter shape.
- [Install-Into-Project](Install-Into-Project) — install the harness into a project.
- [Cut-A-Release](Cut-A-Release) — cut a tagged release via `ship-release`.
- [ADR 0001](0001-phase-gated-workflow) — why phase gates.
- [ADR 0002](0002-documentation-convention) — why wiki + phase-boundary docs + installer boundary.
