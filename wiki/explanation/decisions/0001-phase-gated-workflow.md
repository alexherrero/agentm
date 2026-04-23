# ADR 0001: Phase-gated workflow

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-03-01

## Context

An AI coding agent handed a brief like "add feature X" will, by default, collapse the whole software lifecycle into one conversation: read, plan, implement, review, commit, and sometimes even release. This fails in predictable ways:

- **Plan-and-implement share context**, so the plan gets silently bent by implementation difficulty ("this is hard, let's make it smaller") instead of the user re-scoping.
- **Implement-and-review share context**, so the reviewer anchors on the implementer's reasoning and rubber-stamps (documented rubber-stamping effect: >50% degradation when rebutted — [Challenging the Evaluator, 2025](https://arxiv.org/html/2511.10871v1)).
- **All-of-the-above shares a token budget**, so compaction kicks in mid-feature and the agent loses what it knows.
- **The failure mode is unobservable** — when the session goes sideways, the user can't tell which stage broke.

The published prior art — Trail of Bits' config, Chachamaru127's harness, Anthropic's own harness-design post — all converge on the same answer: hard phase boundaries with fresh sessions at each boundary.

## Decision

Every session runs **exactly one** of six phases: `setup`, `plan`, `work`, `review`, `release`, `bugfix`. The boundaries are enforced by slash commands on Claude Code and by phase-specific entry prompts on the other adapters. Each phase:

1. Reads its inputs from on-disk state ([`.harness/PLAN.md`](https://github.com/alexherrero/agentic-harness/blob/main/templates/PLAN.md), `features.json`, `progress.md`).
2. Has one success criterion (not several).
3. Ends by writing on-disk state so the next session can pick up.

Within `/work` specifically: **one task per session**, even if the next task looks easy. Scope creep across task boundaries is the single failure mode this rule is designed to prevent.

## Consequences

**Positive**

- **Each phase can be evaluated independently.** When something fails, the observed-vs-expected delta is small.
- **Fresh context at each boundary** avoids compaction mid-decision. Costs more tokens upfront in exchange for decisions being made with full context.
- **State survives session death.** A crashed or rate-limited session doesn't lose the plan — it's already on disk.
- **The reviewer never sees the implementer's reasoning trace**, which is a precondition for adversarial review actually finding bugs.
- **Adapter parity is achievable.** Each adapter just needs to expose six entry points that read the same canonical specs.

**Negative**

- **Users who want a one-shot "build this for me" experience will be frustrated.** The harness refuses to do it. This is intentional (see [Product-Intent](Product-Intent) "Non-goals") but it is a real cost.
- **Starting a new session costs wall time.** The harness accepts this; fresh context is worth the 10-20 seconds.
- **Phase-boundary state must be kept consistent.** If `PLAN.md` and `progress.md` disagree, the next session is confused. Mitigation: every phase spec ends with an explicit on-disk update step.
- **Enforcement is soft.** Nothing stops an agent from implementing two tasks in a `/work` session — it's a norm, not a lock. We rely on the spec being in the agent's context and the user calling it out.

**Load-bearing assumptions** (re-check on every model bump, per [principle 6](https://github.com/alexherrero/agentic-harness/blob/main/harness/principles.md#6-re-audit-the-harness-on-every-model-bump))

- Models still benefit from fresh context over compacted long sessions.
- Models still rubber-stamp when shown the implementer's reasoning.
- On-disk state files are still cheaper to re-read than conversation history is to compact.

If a future model can genuinely hold 10 phases of context coherently, some of this scaffolding stops being load-bearing. The phase boundaries themselves are likely to survive longer than the one-task-per-session rule.
