# Product intent

What Agent M is, who it's for, and the shape of the problem it solves — the "why this repo exists" page, readable in five minutes. The deeper reasoning lives in [`harness/principles.md`](https://github.com/alexherrero/agentm/blob/main/harness/principles.md) and the [Decisions](Decisions).

## The problem is scaffolding, not the model

A modern coding agent can produce a working feature inside a single long conversation. What it usually *can't* do in that same conversation is stop before scope creeps, run deterministic gates before declaring success, leave a successor enough state to pick up cleanly, review its own work adversarially instead of rubber-stamping, and keep the docs honest after the code ships.

None of those are model problems — they don't get better with a bigger model. They're scaffolding problems, and they get better with better scaffolding. Agent M *is* that scaffolding: a phase-gated workflow plus an on-disk state layout that any `AGENTS.md`-aware agent can follow the same way.

## The shape: phases with hard boundaries

The development lifecycle is split into discrete phases, each with one job and an exit gate. You don't write code in the plan phase, and you don't merge in the work phase.

| Phase | Job | Ends with |
|---|---|---|
| `/setup` | First-time scaffold, feature list, `init.sh`. | Directory layout + a `PLAN.md` stub. |
| `/plan` | Turn a brief into tasks with verification criteria. No code. | `PLAN.md` with tasks. |
| `/work` | Work the plan's tasks autonomously — safety-gate each, gates green + commit per task; stop only on a failed check or a needed clarification. | Tasks `[x]`; `progress.md` lines appended. |
| `/review` | Adversarial critique — must produce a failing test or a line-number defect. | An executable artifact, not prose. |
| `/release` | Pre-merge gate: clean tree, full suite, feature flags flipped truthfully. | A verified-ready state; no push. |
| `/bugfix` | A Report → Analyze → Fix → Verify *pipeline* for bugs, used instead of `/plan` + `/work`. | A fix committed with a regression test. |

`/bugfix` is a pipeline rather than a phase, but it sits alongside the five phases as the sixth command an operator reaches for. A separate skill, [`ship-release`](https://github.com/alexherrero/agentm/blob/main/harness/skills/ship-release.md), cuts a tagged GitHub release *after* `/release` passes — semver sized from the conventional-commit prefixes since the last tag.

## Who it's for

Someone who pays per token and per minute, values minimal ceremony over a 150-agent supermarket, and wants the *same* workflow across [every supported host](Compatibility) so their muscle memory travels. Someone who treats tests and typecheckers as the truth and LLM reviews as augmentation — and who is willing to spend five minutes at the end of each feature keeping the docs honest, in exchange for never having to re-derive the system later.

## The principles, in short

The full text is in [`harness/principles.md`](https://github.com/alexherrero/agentm/blob/main/harness/principles.md); each has its own decision record under [Decisions](Decisions).

1. **Phase-gated workflow over free-form conversation.** Each session does one thing; fresh context at boundaries beats compaction.
2. **State lives on disk, not in context.** `PLAN.md`, `features.json`, `progress.md` — the next session starts by reading.
3. **Single-threaded for coherence; fan-out only for read-only breadth.** Parallel implementers produce inconsistent decisions; parallel readers are fine.
4. **Deterministic verification before LLM judgment.** Typecheck → lint → test → build, *then* an optional critic. A review that skips gates is not a review.
5. **Adversarial review with an "assume bugs" framing.** The reviewer must produce an executable artifact, not prose. Neutral reviewers rubber-stamp.
6. **Re-audit the harness on every model bump.** Scaffolding that was load-bearing last quarter often isn't anymore.

## What it deliberately is not

- **A one-shot "build me a feature" agent.** The harness refuses to plan, implement, and review in a single session.
- **A supermarket of agents.** The harness keeps its own roster small on purpose — read-only explorers, the adversarial reviewers, a `documenter`, and a focused skill set — because every addition costs coherence. The current set is in [Repo layout](Repo-Layout).
- **A replacement for tests or code review.** Deterministic gates come first; LLMs augment.
- **Dynamic doc generation from code.** Docs are human-edited narrative, refreshed by the `documenter` at phase boundaries only — see [ADR 0002](seven-section-convergence).
- **Universal across every tool.** The harness targets `AGENTS.md`-aware hosts; a host without an adapter tree needs one written per [Repo layout](Repo-Layout).

## Related

- [How the pieces fit](How-The-Pieces-Fit) — how phases, adapters, templates, and scripts interact.
- [Repo layout](Repo-Layout) — the on-disk map and the current adapter/agent/skill roster.
- [Compatibility](Compatibility) — the supported hosts and the OS matrix.
- [ADR 0001 — Phase-gated workflow](agentm-hld) · [ADR 0002 — Documentation convention](seven-section-convergence).
