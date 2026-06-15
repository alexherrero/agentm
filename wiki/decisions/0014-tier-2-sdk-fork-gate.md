# ADR 0014 — The Tier-2 gate: don't fork the loop through the Agent SDK

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-14

## Context

The token-efficiency initiative (#46) splits the savings into two tiers. **Tier-1** trims what every session carries and re-reads — the always-load floor (`CLAUDE.md` / `AGENTS.md` / always-load memory), a per-recall token budget, heat-based curation, a status-line cost meter, and model routing. Tier-1 needs nothing from the model surface beyond what the Claude Code CLI already gives, and by the time this gate is reached it has largely landed. **Tier-2** is the set of API-level levers that the CLI did *not* expose when the initiative was scoped: **context editing** (`clear_tool_uses`, which *clears* stale `tool_use` / `tool_result` blocks rather than summarizing them), the **1-hour prompt-cache TTL**, and **per-task token budgets**.

The pre-audit premise was blunt: those three "84%-class" wins were Agent-SDK / raw-API only, so *reaching the top of the 50–75% savings band likely needs running the loop through the Agent SDK instead of the CLI* — trading the savings ceiling against losing the CLI's hooks, skills, and the crickets developer-workflows / code-review plugin surface. That is a strategic architecture call, not a code task, which is why it is recorded here as a gate before any Tier-2 implementation is planned.

Two findings, gathered for this decision, move the premise.

**The CLI exposes none of the three levers natively — but one is already captured anyway.** A live-docs re-verify (2026-06-14) confirmed the CLI still has no configurable context-editing and no per-task token budget; the task-budget docs state outright that budgets are "not supported on Claude Code or Cowork surfaces." Its native auto-compaction is *summarization*, not *clearing*, and its threshold is not operator-tunable. The exception is the **1-hour cache TTL: Claude Code already requests it automatically on a subscription plan** — which is exactly this operator's setup. So the marathon / think-gap lever is already had for free, and only two of the three levers — context-editing and task-budgets — remain genuine CLI gaps.

**Measured cache behaviour inverts the worry that drove the premise.** The pre-audit assumed coding would sit *below* the vendor's 84% cache-served headline, because coding re-references earlier reads. Running Part B's analyzer over four real agentm / crickets build sessions (June 2026) shows the opposite:

| session | cache-served | cache-read volume | fresh input |
| --- | --- | --- | --- |
| crickets (heavy) | 97.0% | 4.26B tokens | 7.4M (0.2%) |
| agentm (heavy) | 94.6% | 1.70B tokens | 4.0M (0.2%) |
| agentm | 97.9% | 1.64B tokens | 6.6K (0.0%) |
| crickets | 94.4% | 672M tokens | 6.3M (0.9%) |

Cache-served clusters at **~95–97%, above the 84% headline, not below it.** The cache *hit rate* is already near its ceiling — the 1h-TTL is demonstrably working across think-gaps — and spend is overwhelmingly **cache-read volume**: billions of tokens re-read per session at the cheap cache-read rate, dwarfing fresh input. The always-cached floor (system prompt + `CLAUDE.md` / `AGENTS.md` + tool schemas + always-load memory) measures ~16K–48K tokens re-read every turn — small against the accumulated conversation-and-tool-output that makes up the rest of the volume. (Two sessions show a $0 dollar total from a pricing-table gap on an unpriced model string; the percentages are computed from raw token counts and are unaffected.)

The decision number is therefore **not** "how much can we lift the cache-hit rate" — that lever is spent. It is "how much of the cache-read *volume* can we cut," and which lever cuts it. Of the Tier-2 three: the 1h-TTL is already captured; task-budgets is a runaway-spend guardrail, not a volume reducer; and only **context-editing** structurally attacks the re-read volume, by clearing stale tool output so it stops being re-read every turn.

**Open questions this decision resolves:**

- Should agentm's loop run through the Agent SDK to unlock Tier-2, and if so how much of it — full fork, no fork, or a subset?
- Given the measured ceiling, what is the realistic Tier-2 upside, and which lever actually delivers it?
- What happens to the residual cache-read-volume cost if we *don't* fork — is it strandable on the CLI?
- Does the compression track (the deferred Part F: append-only tool-output offload with symbolic refs) get built, and on what trigger?

## Decision

### 1. Don't fork the interactive loop through the Agent SDK (DC-1)

agentm's interactive loop stays on the Claude Code CLI. A full fork is rejected.

**Why not fork:** the fork's entire payoff was the "84%-class" cache wins, and the measurement shows those are mostly already had — cache-served is 95–97% on the CLI, and the one marathon lever the fork would unlock (1h-TTL) is already automatic on the subscription. Against that thin, mostly-captured gain, a full fork forfeits the whole surface agentm is built on: the verification hooks (the `check-all.sh` gate battery wired through Write/Edit hooks), the skills, the crickets developer-workflows and code-review plugin loop, and the memory engine's own session hooks. Trading the entire harness for a marginal, already-realized cache delta is a bad trade at any discount.

### 2. Treat the three levers asymmetrically — the 1h-TTL is done, only two gaps remain (DC-2)

The decision does not lump the three Tier-2 levers together. The **1h cache TTL is already captured** (automatic on subscription; the measured 95–97% hit rate proves it spans think-gaps) and needs no action. The genuine CLI gaps are **context-editing** and **task-budgets** — and they are not equal: context-editing reduces re-read volume (the dominant cost), while task-budgets only caps runaway spend.

**Why not treat them as one bundle:** bundling is what made the fork look necessary — "three levers, all SDK-only, so fork to get them." Disaggregated, two-thirds of the bundle either is already had (1h-TTL) or is a guardrail rather than a savings multiplier (task-budgets). Only context-editing is both genuinely unavailable and genuinely volume-cutting, which narrows the entire fork question down to a single lever's incremental value.

### 3. Capture the residual cache-read-volume savings through Tier-1 on the CLI, not a fork (DC-3)

The residual cost — cache-read volume — is attacked with the Tier-1 levers that need no fork: floor trim (`CLAUDE.md` / `AGENTS.md` / always-load memory), the per-recall token budget, heat-based always-load curation, model routing, and the status-line meter. These shave the always-cached floor and the recalled-context volume off every turn while the loop stays on the CLI.

**Why not the subset-fork here instead:** part of the same volume reduction is reachable without leaving the CLI, and Tier-1 is already landing. Spending the architectural cost of an SDK path to chase volume that floor-trim and recall-budgeting already reach would be paying twice. The fork is reserved (DC-4) for the one slice Tier-1 *cannot* reach.

### 4. The subset-fork is the designated escalation — deferred behind a measurement gate, not built now (DC-4)

A **subset-fork** — an SDK-backed background worker for token-heavy *autonomous* runs, with interactive work staying on the CLI — remains a first-class option, but is deferred. It is reopened only when a bounded **measurement spike** shows context-editing reclaims materially more cache-read volume than the CLI's native auto-compaction already does, on a representative replayed autonomous session. If the spike clears the bar, build the subset-fork (preserving the hook / skill / plugin surface on the worker — e.g. via the Agent SDK or Managed Agents); if not, close it.

**Why not build the subset-fork now (and why not reject it outright):** its entire value rests on context-editing's *incremental* reduction over native auto-compaction — a counterfactual that cannot be read off existing transcripts, because the transcripts never ran context-editing. Building speculatively risks the plugin-surface loss for an unquantified gain; rejecting outright would discard the one genuinely-unavailable, genuinely-volume-cutting lever. A measurement gate is the honest middle: it converts the open question into a number before any architecture is committed.

### 5. Defer Part F (the bespoke compression track) behind the same gate (DC-5)

The deferred compression track — append-only tool-output offload with symbolic references — is **not** built now. It is gated on the same measurement as the subset-fork: only if context-editing (once reachable) is shown to leave tool-output volume still dominant does a bespoke compression layer get revisited.

**Why not build compression now:** it targets the same cache-read volume that native auto-compaction and (eventual) context-editing already address, and the measurement gives no evidence that a custom layer is needed on top. Building it speculatively is the most expensive way to discover it was redundant. If it is ever built, the constraint stands: append-only offload with symbolic refs only, never per-turn rewrites (per-turn rewrites would invalidate the cache prefix and destroy the very 95–97% hit rate this ADR rests on).

### 6. Approximate the task-budget guardrail on the CLI; don't fork for a hard cap (DC-6)

The runaway-spend protection that per-task budgets would give — measured 5h windows hit $400–585 on heavy build days — is approximated on the CLI with the status-line cost meter (Part C), `/clear` discipline at phase boundaries, and model routing (Part D). A hard per-task cap is not worth a fork on its own.

**Why not fork for the hard cap:** a budget's 20K-token floor and abort-on-exceed behaviour is a guardrail, not a savings engine — it prevents the worst window, it doesn't lower the median. The CLI approximations cover the same failure mode (a session running away unnoticed) without the surface loss, so the cap alone never justifies the fork.

## Consequences

**Positive**

- **The harness stays whole.** Hooks, skills, the crickets plugin loop, and the memory engine's session hooks all keep working, because the loop never leaves the CLI — the surface agentm is built on is preserved by construction.
- **The decision is grounded in measured behaviour, not the vendor headline.** Cache-served is measured at 95–97% on this harness's real mix, above the 84% figure, so the ADR rests on what this operator's sessions actually do.
- **The fork question is reduced to one measurable lever.** Disaggregating the three levers (DC-2) collapses a vague "fork to get the SDK wins" into "does context-editing beat native auto-compaction by enough" — a number, gated (DC-4), not a vibe.
- **No speculative spend.** Neither the subset-fork (DC-4) nor the compression track (DC-5) is built without a measurement clearing the bar, so the expensive paths are taken only when shown to pay.

**Negative**

- **The one genuinely-unavailable high-value lever (context-editing) is left on the table for now.** If its incremental reduction over auto-compaction turns out large, this ADR will have delayed a real win until the measurement spike runs. The gate is deliberate, but it is a deferral.
- **Tier-1 carries the whole load.** With the fork off the table, hitting the 50–75% target depends on floor-trim + recall-budget + heat-policy + model-routing actually delivering. If Tier-1 underperforms, there is no Tier-2 fallback in `main` until the gate is reopened.
- **The CLI guardrails are softer than a hard budget.** `/clear` discipline and a status-line meter rely on the operator (or session conventions) reacting; they will not abort a runaway window the way a per-task budget would.

**Load-bearing assumptions (with re-audit triggers)**

- **Cache-served stays near-ceiling (~95–97%) on real sessions.** The whole "the cache layer is already optimized, only volume remains" argument depends on it. **Re-audit trigger:** measured cache-served drops materially below ~90% on representative sessions (a caching regression, or a workload shift that re-references far less) — then the hit-rate lever is back in play and the fork calculus changes.
- **The 1h-TTL stays automatic on the subscription.** DC-2 treats it as captured. **Re-audit trigger:** Claude Code stops requesting the 1h TTL by default, or the operator moves to an API-key / third-party-gateway setup where it is opt-in — then it becomes an actual lever again.
- **Context-editing's incremental value over native auto-compaction is unproven.** DC-4 and DC-5 hang on it. **Re-audit trigger:** the CLI exposes context-editing or task-budgets natively (then adopt them in place — the fork gap closes and this gate retires), **or** Tier-1 fully lands and measured cache-read volume on autonomous runs still dominates cost (then run the spike).
- **The full CLI surface remains worth more than the fork's gain.** The premise of DC-1. **Re-audit trigger:** Anthropic ships an SDK / Managed-Agents path that natively carries hooks + skills + the plugin surface, collapsing the "fork loses the harness" cost — then a fork stops forfeiting the surface and must be re-weighed.
- **Scaffolding decays with the model.** **Re-audit trigger:** the underlying model ships a new major version — re-audit this gate end to end (the standing harness-maintenance principle).

## Related

- [ADR 0011 — V5 unbundling: slim the dev loop](0011-v5-unbundling-dev-loop.md) — established that the memory engine is what only agentm provides and the phase loop lives in crickets; a full fork would forfeit exactly that plugin surface, which is the core of DC-1's "why not."
- [ADR 0006 — Split customizations into `crickets`](0006-crickets-split.md) — the plugin surface (developer-workflows, code-review) a fork would strand; named in DC-1.
- [CI gates](CI-Gates) — the `check-all.sh` battery wired through Write/Edit hooks that a fork off the CLI would lose.
- #46 (token-efficiency by default) — the parent initiative; Tier-1 (floor-trim, per-recall budget, heat policy, status meter, model routing) is the non-fork path DC-3 routes the residual savings through.
