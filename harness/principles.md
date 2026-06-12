# Principles

The design calls behind this harness. Written down so they can be argued with and revised — not decorative.

## 1. Phase-gated workflow over free-form conversation

A single session should do exactly one of: scaffold, plan, implement, review, release. The boundaries exist because:

- Each phase has a different success criterion. Mixing them makes all of them worse.
- Fresh context at each boundary is cheaper and more reliable than trying to compact across roles. ([Trail of Bits' fresh-session discipline](https://github.com/trailofbits/claude-code-config) — "interview first, implement second, in a clean session.")
- When something goes wrong, you can see *which phase* broke.

**The five verbs:** `setup / plan / work / review / release`, plus a `bugfix` pipeline for triage-first work. Borrowed from [Chachamaru127/claude-code-harness](https://github.com/Chachamaru127/claude-code-harness).

## 2. State lives on disk, not in context

Context is ephemeral. Files are durable, diffable, resumable. The harness mandates four on-disk artifacts per project:

- `.harness/PLAN.md` (or a named `PLAN-<name>.md`) — an active goal and its task decomposition with verification criteria. A solo session uses the unnamed `PLAN.md`; concurrent workers each own a distinct named plan.
- `.harness/features.json` — structured feature list with `{ description, steps, passes: bool }` per feature. JSON because Anthropic found models are less likely to inappropriately edit JSON than Markdown.
- `.harness/progress.md` — append-only log of completed work. Starts every new session by reading this.
- `.harness/init.sh` — pre-written script to boot the dev environment. Saves context on every session start.

**Rule:** every phase ends with an on-disk update. A session that leaves no trace is a session the next agent cannot pick up.

## 3. Single-threaded for coherence, fan-out only for read-only breadth

From Cognition's ["Don't Build Multi-Agents"](https://cognition.ai/blog/dont-build-multi-agents): coding is coherence-critical. Parallel implementers produce mutually-inconsistent decisions that the orchestrator cannot reconcile (the "Flappy Bird with Mario background" failure).

But parallel *read-only* sub-agents are fine and often strictly better than sequential reads — they compress independent regions of the codebase before the main agent synthesizes. This is why Claude Code's Task tool exists.

**Rule:** sub-agents gather context; they never write code. One implementer per `/work` session.

## 4. Deterministic verification before LLM judgment

LLM judges are sycophantic under pressure and degrade >50% when rebutted ([Challenging the Evaluator, 2025](https://arxiv.org/html/2511.10871v1)). Typecheckers and tests are neither sycophantic nor expensive.

**Ordering for the `/review` phase:**
1. Typecheck
2. Lint
3. Unit tests
4. Integration tests (if they exist)
5. Build
6. *Then* the adversarial LLM reviewer, for things the above can't see (API design, spec adherence, subtle logic, security issues without a lint rule).

A `/review` that skips steps 1–5 is not a review.

## 5. Adversarial review with "assume bugs" framing

From every serious writeup on LLM review: neutral-prompted reviewers rubber-stamp. The reviewer must be told *the code likely contains bugs, find them*. Additionally:

- **Reviewer gets artifact + spec only, not the implementer's reasoning trace.** Otherwise it anchors on the implementer's justifications.
- **Reviewer must produce an executable artifact** — a failing test, a specific line-number defect, a reproducible counter-example input. Prose critiques fluff; executable ones don't.
- **Log the rejection rate.** A reviewer with <10% rejections over a sample is broken (or the implementer is superhuman — far less likely).

## 6. Re-audit the harness on every model bump

From [Anthropic's harness design post](https://www.anthropic.com/engineering/harness-design-long-running-apps): *"When a new model lands, it is generally good practice to re-examine a harness, stripping away pieces that are no longer load-bearing."*

Scaffolding that was essential for one model generation is often just overhead on the next. The harness should get *simpler* over time, not more elaborate.

**Rule:** every time you adopt a new default model, spend 30 minutes running a "what's still load-bearing?" pass. Delete anything that isn't.

## 7. Simplicity first

From the same article: *"Find the simplest solution possible, and only increase complexity when needed."* This harness exists to encode the minimum structure that consistently produces good work. When tempted to add a seventh phase, a third sub-agent, a new template — ask: "what specific failure am I trying to prevent, and have I seen it happen?" If the answer is hypothetical, don't add it.

## Non-principles (things we explicitly reject)

- **Multi-agent dev-team role-play** (PM / Architect / Dev / QA as separate agents). Coordination cost without matching benefit outside benchmarks.
- **Parallel implementer fan-out.** Merge hell. Use a single implementer and iterate.
- **LLM-as-judge as a final gate.** Always backed by deterministic checks.
- **100+ subagent libraries.** Pick the two or three you actually use.
- **Elaborate message buses / event streams.** A plan file and git history are the coordination primitive.
