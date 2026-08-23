# Slop labelling — your 34

Read [RUBRIC.md](RUBRIC.md) first. It is frozen; if it turns out to be
wrong, say so and we void the labels rather than patching it.

For each note write one of `expire` / `review` / `keep` / `unsure` on its
**answer line**. Thirty seconds each is the intended pace — first
impression is the measurement, and `unsure` is a real answer.

The decision procedure, in short: does it make a claim you could act on
or be wrong about? Then `keep`, and stop. Otherwise: unfilled skeleton
*and* near-copy → `expire`; skeleton alone, or many words saying nothing
→ `review`; anything else → `keep`.

Short and dense is **not** slop. Neither is a bare reference, a badly
written note carrying a real claim, or an old one.

---

## 01

```
---
type: reference
status: active
captured: 2026-08-12T05:22:27Z
updated: 2026-08-12T05:22:27Z
slug: the-always-on-agent-reads-up-to-50-recent-memories-per-query-which-is-it
title: The Always-On agent reads up to 50 recent memories per query, which is its real ceiling
tags: [agentm-comparison, memory-architecture, retrieval, scaling]
aliases: ["does the google memory agent scale", "how many memories can it hold", "what breaks in read-all memory architectures", "why not just read every memory"]
source: "https://www.marktechpost.com/2026/07/18/google-clouds-always-on-memory-agent-replaces-rag-and-embeddings-with-continuous-llm-consolidation-on-gemini-3-1-flash-lite/"
---

Its QueryAgent answers by reading all memories and consolidation insights — bounded in practice to the 50 most recent, a limit the project's own comparison table lists as its main limitation, in the same column where it lists RAG's passivity. That is the number to hold onto: the architecture is read-everything, so the corpus it serves has to stay small enough to read. agentm indexes ~9,900 notes and searches them, which is a different regime, not a better implementation of the same one.
```

**01 answer:** 

---

## 02

```
---
type: reference
status: active
captured: '2026-07-19 20:09:04+00:00'
updated: '2026-07-19 20:09:04+00:00'
slug: towards-structural-understanding-of-llm-overthinking-google-deepmind
tags: [skill-discovery, web]
source: https://deepmind.google/research/publications/203490/
evaluator_classification: MEDIUM
rubric_score: 2
---
# Towards Structural Understanding of LLM Overthinking — Google DeepMind

July 2, 2026 Towards Structural Understanding of LLM Overthinking View publication Download Share Copied Abstract Models employing long chain-of-thought (CoT) reasoning have shown superior performance on complex reasoning tasks. Yet, this capability introduces a critical and often overlooked inefficiency: overthinking. Models often engage in extensive reasoning even for simple queries, incurring s

Source: https://deepmind.google/research/publications/203490/
```

**02 answer:** 

---

## 03

```
---
type: preference
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: never-stop-to-ask-approval-to-1
always_load: false
derived_from: [personal/_inbox/never-stop-to-ask-approval-to-1.md, personal/_inbox/never-stop-to-ask-approval-to-2.md, personal/_inbox/never-stop-to-ask-approval-to-3.md, personal/_inbox/never-stop-to-ask-approval-to-4.md, personal/_inbox/never-stop-to-ask-approval-to-5.md, personal/_inbox/never-stop-to-ask-approval-to.md]
---


User stated: ...m to Completed/SHIPPED, update staging notes) is **recoverable → autonomous** — never stop to ask approval to archive or to do close-out bookkeeping. **Carve-outs — unchanged by this doctrine.** W...
```

**03 answer:** 

---

## 04

```
---
type: convention
status: active
captured: '2026-05-25'
updated: '2026-05-25'
slug: pre-approval-batch-pattern
tags: [work-phase, operator-review, velocity, stop-conditions, always-load-graduate]
---

The operator routinely pre-approves task batches to keep velocity high. Phrases observed across plans: *"let's do task 1-7 straight through I approve in advance"*, *"proceed with tasks 1 straight through to six with my approval, as before only stop if you need to ask me questions"*, *"lets do from task 1 through 5 straight, i pre-approve them all, stop only to ask for dangerous actions or questions that need my input or clarification."*

**Default mode after pre-approval is given:** execute through all tasks in sequence without stopping at the operator-review-and-approve gate between tasks. Report progress per task (status report shape; commit SHA, CI status, etc.) but don't ask "ok to proceed?" between tasks.

**Stop conditions remain in effect:**
1. **Dangerous actions** — `gh repo rename`, `git push --force`, repo deletes, irreversible operations against external services. Always confirm.
2. **Genuine ambiguity** — something the plan didn't anticipate; can't be resolved without operator input.
3. **Gate failures** that can't be self-recovered within the 5-iteration cap.
4. **Scope creep** — task turns out to be bigger than planned. Stop and surface per `/work` non-negotiables.
5. **Unexpected resource implications** — large data deletes, API budget concerns, expensive CI cycles.

**Anti-pattern:** asking *"ok to proceed?"* between every task after pre-approval was given. Burns the operator's time and breaks flow. The pre-approval was given precisely so the operator can be hands-off.

**How to apply:**
- Treat the pre-approval phrasing as a contract that overrides the per-task review gate in [`harness/phases/03-work.md`](harness/phases/03-work.md), but does not override the stop conditions above.
- When in doubt about whether to stop: bias toward stopping. The operator can always say "keep going" but can't un-do a dangerous action.

**Re-audit trigger:** if a pre-approved batch lands a regression that should have been caught at the inter-task review gate, revisit whether the pre-approval scope was too broad.
```

**04 answer:** 

---

## 05

```
---
kind: opinion-supplement
status: expired
created: 2026-07-26T02:15:48+00:00
slug: no-handoff-pack-the-cap-is
opinion: efficient
sessions: [-Users-alex-Antigravity-agentm/41498e3e-3550-438d-ba85-0b6516299d26]
mining_confidence: LOW
mining_rationale: "user correction signal"
mining_occurrences: 1
---

## Correction: no handoff pack) — the cap is never silently longer just bec

User corrected the agent: ...gate. If token-audit is absent, the tripwire still fires (degraded: loud stop, no handoff pack) — the cap is never silently longer just because the capability is missing. 6. **Do not silently expand task scope.** If it turns out bigger than planned,...

## Mining metadata

- **Proposed supplement to**: `efficient`
- **Category**: `preferences`
- **Confidence**: `LOW`
- **Rationale**: user correction signal
- **Occurrences**: 1


## Supporting excerpts

> ...gate. If token-audit is absent, the tripwire still fires (degraded: loud stop, no handoff pack) — the cap is never silently longer just because the capability is missing. 6. **Do not silently expand task scope.** If it turns out bigger than planned,...
```

**05 answer:** 

---

## 06

```
---
type: preference
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: never-because-it-makes-the-code-1
always_load: false
derived_from: [personal/_inbox/never-because-it-makes-the-code-1.md, personal/_inbox/never-because-it-makes-the-code-2.md, personal/_inbox/never-because-it-makes-the-code-3.md, personal/_inbox/never-because-it-makes-the-code-4.md, personal/_inbox/never-because-it-makes-the-code-5.md, personal/_inbox/never-because-it-makes-the-code-6.md, personal/_inbox/never-because-it-makes-the-code.md]
---


User stated: ...reproduce the test's intent independently; delete only if the intent is wrong, never because it makes the code fail. | ### 2.5. Per-task isolation check (worktree-per-task mode only) **Only whe...
```

**06 answer:** 

---

## 07

```
---
type: convention
status: active
captured: '2026-05-19'
updated: '2026-07-06'
slug: worktrees-never-auto
tags: [git, host-behavior, claude-code, always-load-graduate]
---

Never create git worktrees automatically. Work directly on the current branch (typically `main`).

**Why:** The user manages branch state explicitly and finds spontaneous worktree creation disorienting — it fragments the working tree and creates dangling worktree dirs to clean up later. The agent's `EnterWorktree` tool exists for cases the operator explicitly wants isolated sessions, not as a default safety mechanism.

**How to apply:** Do not call `EnterWorktree` unless the user explicitly asks for a worktree session in this turn. When proposing an approach that could benefit from isolation (e.g. risky refactors), surface the option as a question rather than acting on it.

**Per-project override (worktree-native flow, ratified 2026-07-06 — see [[worktree-native-verdict-draft]]):** `agentm` + `crickets` carry a durable `isolation.mode: worktree-per-plan` opt-in in a local `.harness/project.json` — in those two repos only, `/work` auto-spawns a worktree per plan via the host's own primitive and auto-merges the plan PR on green. This entry's default (never auto-spawn) still governs every other repo, and every ad hoc session in agentm/crickets outside the `/work` loop.

Source: `~/.claude/CLAUDE.md` § Worktrees.
```

**07 answer:** 

---

## 08

```
---
type: reference
status: active
captured: '2026-06-15'
updated: '2026-06-15'
slug: reference-checklists
tags: [skill-discovery]
source: https://github.com/addyosmani/agent-skills/tree/main/references
evaluator_classification: MEDIUM
rubric_score: 3
---

## What this pattern is

A "progressive disclosure" companion-file architecture where skills load reference
material on demand rather than embedding it inline. Concretely: four reference files
(testing-patterns, security, performance, accessibility checklists) that skills pull
in only when the relevant task context requires them.

## Why this might be worth adopting

The vault's crickets conventions already adopted the anti-rationalization / red-flags /
verification *section headers* from addyosmani/agent-skills (§ E7–E8, see
`projects/crickets/conventions.md` lines 95–110), but the *companion-reference-file
architecture* — the pattern of having a `references/` directory of on-demand checklist
docs that skills load lazily — has no direct equivalent yet. The upcoming
`skill-quality-enforcement` build cycle (ROADMAP-MASTER ⑥, crickets 3.x) will produce
a canonical `templates/skills/SKILL.md` skeleton and a check-skill-body gate; at that
point, the question of where load-bearing reference content lives (inline in the skill
body vs. in a companion file) becomes a real design choice. This watchlist entry
surfaces the addyosmani pattern as a concrete answer to that design choice when it
arrives. The source repo is MIT-licensed, from Addy Osmani (ex-Google Chrome), and is
described as actively maintained per caller-supplied trust signals — credible provenance
despite the null GitHub API fields in the enriched JSON (enrichment failure at Pass 1).
Cross-citation count is 0, so independent validation is thin.

## What would need adapting for personal use

The four reference files are written for a JavaScript/React/web stack: testing-patterns
covers Jest/RTL/Cypress, performance covers Core Web Vitals and Lighthouse, and
accessibility covers browser-based ARIA testing. The operator's active projects use Go
(sherwood trading platform) and Python (agentm harness). Only the security checklist
(OWASP Top 10, auth, input validation, headers) and the accessibility checklist have
language-agnostic content worth carrying forward.

The architectural pattern — skill loads a `references/` companion file — is directly
adoptable 
…truncated…
```

**08 answer:** 

---

## 09

```
---
type: convention
status: active
captured: '2026-07-09'
updated: '2026-07-09'
slug: para-atomic-zettel-convention
tags: ['34', para, zettelkasten, vault-structure, v6-15, v6-18, always-load-graduate]
---

The vault's top-level organizing structure is PARA — Projects (`projects/`), Areas (`personal/domains`, `personal/preferences`), Resources (the reference/pattern/skill-watchlist folders), Archives (superseded and retired material) — and its note-level unit is the atomic Zettel: one note holds one fragment (one preference, one fix, one idea, one decision), never a bundle of unrelated claims, so a link or a `supersedes:` edge can target something precise. MOCs (`Home.md`, per-folder `_index.md` files) are the human-readable browsing layer on top of that structure, not the source of truth — the frontmatter graph is. This ratifies the synthesis #34's research already converged on (operator-confirmed 2026-07-09); it changes nothing about today's folder layout or capture habits, since both already work this way — the alternative (a fresh top-level scheme) was never on the table because the organic structure already fits. Enforcement — a machine-checked schema that rejects a non-atomic or misplaced note — waits on V6-15's typed-object registry; nothing new is built here.

Related: [[vault-internal-taxonomy]], [[obsidian-vault-paths]].
```

**09 answer:** 

---

## 10

```
---
type: preference
status: active
created: 2026-08-02
updated: 2026-08-02
tags: []
group: personal
slug: prefer-delegating-to-the-real-resolver
fingerprint: 3943fd5f3fd177982e3581eca7fc6eeda4505a756f7eb0530a73a216343fed3f
always_load: false
---

User stated: ...nning the hooks cannot load sqlite-vec" would have surfaced this years earlier. Prefer delegating to the real resolver rather than re-implementing the probe, so the check cannot drift from the behavior. 3. Re-drai...
```

**10 answer:** 

---

## 11

```
---
kind: workflow
status: active
created: 2026-08-20
updated: 2026-08-20
tags: []
group: memory
slug: workflow-bash-673
always_load: false
derived_from: [memory/_inbox/workflow-bash-673.md, memory/_inbox/workflow-bash-674.md, memory/_inbox/workflow-bash-675.md, memory/_inbox/workflow-bash-676.md, memory/_inbox/workflow-write-392.md, memory/_inbox/workflow-write-393.md, memory/_inbox/workflow-write-394.md, memory/_inbox/workflow-write-395.md]
---


The `Bash` tool was invoked 436 times during this session. If this represents a repeatable workflow, capture the sequence + when to use it.
```

**11 answer:** 

---

## 12

```
---
type: reference
status: active
captured: '2026-06-15'
updated: '2026-06-15'
slug: context-engineering
tags: [skill-discovery]
source: https://github.com/addyosmani/agent-skills/blob/main/skills/context-engineering/SKILL.md
evaluator_classification: HIGH
rubric_score: 4
---

## What this pattern is

`context-engineering` is a skill for feeding agents the right information at the right time — covering rules-file layout, context-packing strategies for session startup, and MCP server integration patterns. It addresses the practical decisions an agent builder makes to keep a session coherent without blowing the token budget: what goes in always-load vs. on-demand recall, how to pack context at task-switch time, and how to wire MCP servers so the agent picks up environment state rather than re-asking.

## Why this might be worth adopting

The operator's vault has no existing skill covering agent session management at this level of specificity. The `_always-load/` convention and the `phases-one-per-session` and `vault-memory-overrides-default` files gesture at this domain, but they encode *rules* rather than *patterns* — they tell the agent what to load, not *how to decide* what to pack at session-switch time or how to structure rules files for effective recall. The agentm memory engine (heat policy, recall, adapt-state pipeline) is precisely the domain where better context-engineering doctrine would reduce token waste and improve session coherence — directly relevant to the token-efficiency goal (project_token-efficiency-goal.md). Author credibility is high (MIT, Addy Osmani / ex-Chrome).

## What would need adapting for personal use

The rules-file and context-packing patterns are generic; the adaptation work is mapping them to agentm's specific structures (_always-load vs. session-recall vs. personal-skills) and to the crickets plugin model (where conventions live in SKILL.md files, not AGENTS.md top-level). The MCP integration patterns may not all apply — the operator's MCP usage is server-side (agentmemory, etc.) rather than tool-per-skill — so filter to the relevant subset. The session-startup section is most directly adaptable; the rest is guidance to internalize rather than encode verbatim.

## Source

- **Original**: https://github.com/addyosmani/agent-skills/blob/main/skills/context-engineering/SKILL.md
- **Discovered via**: addyosmani-agent-skills (d
…truncated…
```

**12 answer:** 

---

## 13

```
---
kind: opinion-supplement
status: expired
created: 2026-08-06T04:24:41+00:00
slug: never-anyone-2
opinion: done
sessions: [-Users-alex-Antigravity-agentm/d0df4630-48eb-4603-a4ac-4cd3b507f2f9]
mining_confidence: LOW
mining_rationale: "explicit always/never directive"
mining_occurrences: 1
---

## never anyone

User stated: ...structure, parity, wiring. Live recall quality cannot be a unit test, so it was never anyone's definition of done. The gates were not too strict; they were pointed at the w...

## Mining metadata

- **Proposed supplement to**: `done`
- **Category**: `preferences`
- **Confidence**: `LOW`
- **Rationale**: explicit always/never directive
- **Occurrences**: 1


## Supporting excerpts

> ...structure, parity, wiring. Live recall quality cannot be a unit test, so it was never anyone's definition of done. The gates were not too strict; they were pointed at the w...
```

**13 answer:** 

---

## 14

```
# Trusted source orgs
#
# Used by the adapt-skills workflow to flag '+1 trustworthiness' on
# candidates whose source repo owner matches an entry here. Edit
# freely in Obsidian: one org-slug per non-comment line. Lines
# starting with `#` are comments; blank lines ignored. Org slugs
# match the GitHub URL's owner component (case-insensitive).
#
# Seeded on first install with operator-approved defaults.

anthropics
anthropic
google
googleworkspace
googlecloudplatform
microsoft
vercel
hashicorp
openai
cloudflare
github
supabase
redis
kubernetes
docker
pytorch
huggingface
modelcontextprotocol
```

**14 answer:** 

---

## 15

```
---
type: reference
status: active
captured: '2026-06-15'
updated: '2026-06-15'
slug: doubt-driven-development
tags: [skill-discovery]
source: https://github.com/addyosmani/agent-skills/blob/main/skills/doubt-driven-development/SKILL.md
evaluator_classification: HIGH
rubric_score: 4
---

## What this pattern is

`doubt-driven-development` is an in-flight adversarial self-review loop with the structure CLAIM → EXTRACT → DOUBT (fresh-context subagent) → RECONCILE → STOP. When stakes are high (production, security, irreversible decisions), a separate subagent instance is spun up with no access to the original reasoning trace and asked to disprove the specific claim — not review the whole diff, but attack one decision. Optional cross-model escalation is supported when the in-process reviewer cannot break the claim.

## Why this might be worth adopting

This fills a distinct gap from our existing `/code-review` skill: `/code-review` is post-hoc (reviews a completed diff after implementation), while `doubt-driven-development` is in-flight (challenges a specific decision *while the work is still in progress*, before committing). The fresh-context requirement — explicitly not passing the implementer's reasoning trace — is the key architectural move: it prevents the subagent from anchoring on the same blind spots as the implementer. Our adversarial-reviewer and adversarial-reviewer-cross agents are wired into the post-hoc review loop only; we have no equivalent in-flight primitive. Given that the operator's harness already encodes subagent dispatch patterns (explorer, evaluator, adversarial-reviewer), the mechanical scaffolding to implement this is already present — the gap is the in-flight invocation trigger and the CLAIM→EXTRACT→DOUBT→RECONCILE→STOP contract. Addy Osmani (ex-Chrome team lead, author of "Learning Patterns") brings high author credibility; MIT license. Author trustworthiness is manual-signal HIGH per operator annotation.

## What would need adapting for personal use

The STOP condition (when to escalate to cross-model vs. self-reconcile) would need to be calibrated to the operator's cost-sensitivity (cross-model Gemini calls are non-trivial tokens). The "high stakes" trigger definition needs to be scoped to the harness context: agentm's natural trigger points are (a) before an irreversible gate in `/work` (storage-seam writes, schema migrations, forc
…truncated…
```

**15 answer:** 

---

## 16

```
---
type: convention
status: active
captured: '2026-05-19'
updated: '2026-05-19'
slug: phases-one-per-session
tags: [harness, phase-gated, workflow, always-load-graduate]
---

The agentm is phase-gated. Execute exactly **one phase per session** — do not freestyle across the full development lifecycle in one go.

**Why:** Each phase has different cognitive requirements and different verification gates. `/plan` is design work; `/work` is implementation; `/review` is adversarial critique; `/release` is gating. Mixing them in one session causes the agent to drift into "while I'm here" cleanup that bypasses phase-specific verification (and inflates blast radius). The harness's value comes from the gates, not from convenience.

**How to apply:**

The six canonical phases:

1. **Setup** — first-time scaffold, feature list, `init.sh`. Run once per project.
2. **Plan** — turn a brief into `.harness/PLAN.md` with tasks + verification criteria. **No code written.**
3. **Work** — pick one task from the plan, implement it, update `progress.md`. **Stop.** See [[work-single-task]].
4. **Review** — adversarial critique. Assume the code has bugs. Produce a failing test or specific line-number defect — **not prose.**
5. **Release** — pre-merge gate. Clean tree, all verification passes, changelog updated.
6. **Bugfix** — separate pipeline: Report → Analyze → Fix → Verify. Used **instead of** Plan+Work for bugs.

If the user asks for cross-phase work in a single session, surface the phase-gating expectation and propose either a single phase or sequential phases.

Source: `agentm/AGENTS.md` § Phases (hard boundaries) + Non-negotiable rules.

Related: [[work-single-task]], [[verification-executable-first]], [[state-on-disk]], [[tests-are-sacred]], [[subagents-read-only]].
```

**16 answer:** 

---

## 17

```
---
type: preference
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: never-silently-longer-just-because-the-1
always_load: false
derived_from: [personal/_inbox/never-silently-longer-just-because-the-1.md, personal/_inbox/never-silently-longer-just-because-the-2.md, personal/_inbox/never-silently-longer-just-because-the-3.md, personal/_inbox/never-silently-longer-just-because-the.md]
---


User stated: ...t, the tripwire still fires (degraded: loud stop, no handoff pack) — the cap is never silently longer just because the capability is missing. 6. **Do not silently expand task scope.** If it turns out bigger th...
```

**17 answer:** 

---

## 18

```
---
type: fix
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: yet-because-the-file-was-renamed
always_load: false
derived_from: [personal/_inbox/yet-because-the-file-was-renamed.md]
---


Fix observed: ...yet" because the file was renamed (via `mv`) and never read under its new path. Fixed by reading the first 8 lines of the archive, then re-running the Edit successfully. - No user corrections of approach occurred; the user's guidance was followe...
```

**18 answer:** 

---

## 19

```
---
kind: preferences
status: active
created: 2026-08-20
updated: 2026-08-20
tags: []
group: memory
slug: i-want-to-push-on-the
fingerprint: e817283ac2a0f1db6c22c68e31b7655a6c0e04d5f7c971babfc10c72ace5458d
always_load: false
---

User stated: I want to push on the no llm at ingestion, my other agentkv does this, and it performs the same things that dreaming does once at the start so the original note begin...
```

**19 answer:** 

---

## 20

```
---
kind: opinion-supplement
status: expired
created: 2026-07-26T02:15:46+00:00
slug: never-push-to-a-non
opinion: recoverable
sessions: [-Users-alex-Antigravity-agentm/25a882b0-371c-43dd-914e-3cf7f0e70532]
mining_confidence: LOW
mining_rationale: "explicit always/never directive"
mining_occurrences: 1
---

## Never push to a non

User stated: ...ure operator would need to know (rollback steps, migrations). ## Guardrails - Never push to a non-default branch. - Never overwrite or move existing tags. - Never include uncomm...

## Mining metadata

- **Proposed supplement to**: `recoverable`
- **Category**: `preferences`
- **Confidence**: `LOW`
- **Rationale**: explicit always/never directive
- **Occurrences**: 1


## Supporting excerpts

> ...ure operator would need to know (rollback steps, migrations). ## Guardrails - Never push to a non-default branch. - Never overwrite or move existing tags. - Never include uncomm...
```

**20 answer:** 

---

## 21

```
---
type: preference
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: no-handoff-pack-the-cap-is-1
always_load: false
derived_from: [personal/_inbox/no-handoff-pack-the-cap-is-1.md, personal/_inbox/no-handoff-pack-the-cap-is-2.md, personal/_inbox/no-handoff-pack-the-cap-is-3.md, personal/_inbox/no-handoff-pack-the-cap-is.md]
---


User corrected the agent: ...gate. If token-audit is absent, the tripwire still fires (degraded: loud stop, no handoff pack) — the cap is never silently longer just because the capability is missing. 6. **Do not silently expand task scope.** If it turns out bigger than planned,...
```

**21 answer:** 

---

## 22

```
# NFD regression probe

Delete me.
```

**22 answer:** 

---

## 23

```
---
type: convention
status: active
captured: '2026-06-09'
updated: '2026-06-09'
slug: design-artifacts-to-vault
tags: [dev-flow, design, mockups, vault, always-load-graduate]
---

**Design artifacts must be saved to the vault immediately — never left in scratch dirs.**

When ANY design / prototype / visual pass produces artifacts — mockup HTML or images, design
handoff docs, validators, reference screenshots — save them into the vault at
`projects/<slug>/_harness/designs/` (mockups under `designs/mockups/`) the moment they exist.
This applies to the crickets `/design` skill **and** to external visual passes (Antigravity /
Gemini), which write to scratch dirs (e.g. `~/Antigravity/experimental`) by default.

**Then check the built UI against the canonical mockup** — at design/build time and again at
release (not just the deterministic build gate). Drop a `designs/README.md` naming the canonical
mock + any operator refinements that intentionally diverge from it, so the comparison is auditable.

**Why (the trigger):** on the blog (`alexherrero.dev`) project, the Antigravity "Structured
Ledger" mockups + the two handoff docs + `verify_mockup.py` sat in `~/Antigravity/experimental`
and were never vaulted. The build silently **drifted from the mock** — the hero "Agent M
Dashboard" ended up stacked *under* the title instead of beside it, and the dashboard style +
header icons also differed — and it wasn't caught until the operator eyeballed the live site days
later. The artifacts now live at `projects/blog/_harness/designs/` (canonical:
`mockups/alexherrero-dev-mockup.html`; see that folder's `README.md`).

Applies to any project. Related: `projects/crickets/conventions/design-doc-shape.md`.
```

**23 answer:** 

---

## 24

```
---
type: reference
status: active
captured: 2026-08-12T05:22:27Z
updated: 2026-08-12T05:22:27Z
slug: the-always-on-agent-s-consolidation-loop-is-dreaming-under-a-different-n
title: "The Always-On agent's consolidation loop is dreaming under a different name"
tags: [agentm-comparison, consolidation, dreaming, memory-architecture]
aliases: ["agents that think while idle", "does anyone else do idle memory consolidation", "prior art for dreaming", "who else synthesizes memories on a timer"]
source: "https://www.marktechpost.com/2026/07/18/google-clouds-always-on-memory-agent-replaces-rag-and-embeddings-with-continuous-llm-consolidation-on-gemini-3-1-flash-lite/"
---

A ConsolidateAgent runs on a timer, 30 minutes by default, reviews memories not yet consolidated, finds connections between them, and writes a synthesized summary, one key insight, and the connections back to the database. The write-up calls it sleep cycles and stresses that understanding accumulates while idle with no prompt driving it. Structurally this is agentm's dreaming: an unattended pass that turns atomic memories into synthesis. Independent arrival at the same shape is the useful signal.
```

**24 answer:** 

---

## 25

```
---
type: reference
status: active
captured: '2026-07-19 20:09:04+00:00'
updated: '2026-07-19 20:09:04+00:00'
slug: dreaming-better-memory-for-a-more-helpful-chatgpt
tags: [skill-discovery, feed]
source: https://openai.com/index/chatgpt-memory-dreaming
evaluator_classification: MEDIUM
rubric_score: 2
---
# Dreaming: Better memory for a more helpful ChatGPT

ChatGPT introduces a new memory system to better remember preferences, keeping context fresh and relevant across conversations.

Source: https://openai.com/index/chatgpt-memory-dreaming
```

**25 answer:** 

---

## 26

```
---
type: reference
status: active
captured: '2026-07-19 20:09:04+00:00'
updated: '2026-07-19 20:09:04+00:00'
slug: how-canada-uses-claude-anthropic
tags: [skill-discovery, web]
source: https://www.anthropic.com/research/how-canada-uses-claude
evaluator_classification: MEDIUM
rubric_score: 2
---
# How Canada uses Claude \ Anthropic

Economic Research How Canada uses Claude: Findings from the Anthropic Economic Index Jul 14, 2026 Le français suit. Key findings Based on the latest release of the Anthropic Economic Index, Canada is at the forefront of Claude adoption. Canada represents 2.6% of global Claude.ai traffic and ranks 8th overall by total volume. Usage per capita is more than four times higher than would be expected gi

Source: https://www.anthropic.com/research/how-canada-uses-claude
```

**26 answer:** 

---

## 27

```
---
kind: opinion-supplement
status: proposed
created: 2026-08-17T03:38:03+00:00
slug: never-proceed-silently-87
opinion: efficient
sessions: [-Users-alex-Antigravity-agentm/ba37baf2-b9ba-46d5-a9bc-42931d70e465]
mining_confidence: MEDIUM
mining_rationale: "explicit always/never directive"
mining_occurrences: 3
---

## never proceed silently

User stated: ...r (T3/T4) session triggers `needs_inheritance_pause()` — stop for confirmation, never proceed silently. At `agent_count >= 4`, `announce_dispatch()` also runs the fleet cost gate (`t...

## Mining metadata

- **Proposed supplement to**: `efficient`
- **Category**: `preferences`
- **Confidence**: `MEDIUM`
- **Rationale**: explicit always/never directive
- **Occurrences**: 3


## Supporting excerpts

> ...r (T3/T4) session triggers `needs_inheritance_pause()` — stop for confirmation, never proceed silently. At `agent_count >= 4`, `announce_dispatch()` also runs the fleet cost gate (`t...
> ...urns true, print `inheritance_warning()`'s text and **stop for confirmation** — never proceed silently. At `agent_count >= 4`, `announce_dispatch()` also runs the fleet cost gate (`t...
> ...r (T3/T4) session triggers `needs_inheritance_pause()` — stop for confirmation, never proceed silently. ### 9. Commit One task, one commit, referencing the task. Follow project tra...
```

**27 answer:** 

---

## 28

```
---
kind: opinion-supplement
status: proposed
created: 2026-08-21T14:41:20+00:00
slug: always-goes-through-98
opinion: done
sessions: [-Users-alex-Antigravity-agentm/38202d78-514a-446e-8cfd-cefc94e34d02]
mining_confidence: LOW
mining_rationale: "explicit always/never directive"
mining_occurrences: 1
---

## always goes through

User stated: ...A completed unit of work is **never hard-stopped** by a missing `gh` — the push always goes through. After the helper returns, `ExitWorktree` `keep` (never `remove` — the branch...

## Mining metadata

- **Proposed supplement to**: `done`
- **Category**: `preferences`
- **Confidence**: `LOW`
- **Rationale**: explicit always/never directive
- **Occurrences**: 1


## Supporting excerpts

> ...A completed unit of work is **never hard-stopped** by a missing `gh` — the push always goes through. After the helper returns, `ExitWorktree` `keep` (never `remove` — the branch...
```

**28 answer:** 

---

## 29

```
---
type: reference
status: active
captured: '2026-07-19 20:09:04+00:00'
updated: '2026-07-19 20:09:04+00:00'
slug: did-us-worker-retraining-reduce-participant-automation-exposure-google-deepmind
tags: [skill-discovery, web]
source: https://deepmind.google/research/publications/239849/
evaluator_classification: MEDIUM
rubric_score: 2
---
# Did US Worker Retraining Reduce Participant Automation Exposure? — Google DeepMind

May 6, 2026 Did US Worker Retraining Reduce Participant Automation Exposure? View publication Download Share Copied Abstract This paper evaluates whether the U.S. Workforce Innovation and Opportunity Act (WIOA) supported American worker resilience to technological automation. Analyzing over 23 million WIOA participation records (2017-2023), we introduce the “Retrainability Index,” which measures p

Source: https://deepmind.google/research/publications/239849/
```

**29 answer:** 

---

## 30

```
---
type: preference
status: active
created: 2026-07-25
updated: 2026-07-25
tags: []
group: personal
slug: i-need-to-run-p13-now
fingerprint: f079a667109b29fe89904720c8fae2a5684deae2fd93e887bdbe56ec65f51f11
always_load: false
---

User stated: summarize the order for all the p prompts here? sounds like i need to run P13 now?
```

**30 answer:** 

---

## 31

```
---
type: preference
status: active
created: 2026-07-11
updated: 2026-07-11
tags: []
group: personal
slug: never-the-worker-1
always_load: false
derived_from: [personal/_inbox/never-the-worker-1.md, personal/_inbox/never-the-worker-2.md, personal/_inbox/never-the-worker-3.md, personal/_inbox/never-the-worker-4.md, personal/_inbox/never-the-worker-5.md, personal/_inbox/never-the-worker-6.md, personal/_inbox/never-the-worker.md]
---


User stated: ...djudication is `/review`'s fresh-context design-conformance dimension (Hook 1), never the worker's self-interested call (routing it into the autonomy gate would over- or under-...
```

**31 answer:** 

---

## 32

```
---
type: preference
status: active
created: 2026-06-02
updated: 2026-06-02
tags: []
group: personal-private
slug: i-need-to-consider-rearchitecting-how
always_load: false
---

User stated: ...arness with a single purpose like it is now, is it already setup for that or do i need to consider rearchitecting how this and/or a potential additional repo shoudl look
```

**32 answer:** 

---

## 33

```
---
type: convention
status: active
captured: '2026-05-19'
updated: '2026-05-19'
slug: wake-on-ci-pattern
tags: [dev-flow, ci, verification, always-load-graduate]
---

Don't mark tasks `[x]` speculatively. Push → schedule a ~90s wake → close out with `[x]` only when CI confirms green across the OS matrix.

**Why:** A task marked `[x]` before CI confirms green creates a corruption in the plan state — future sessions reading `progress.md` will trust the closure and proceed on faulty premises. Several past plans caught last-minute Windows/Linux-specific bugs only because the wake-on-CI step forced a confirmation pause (Linux `stat -f` GNU flag trap in plan #7a part 3, pwsh `Start-Process -RedirectStandardInput $null` in plan #7a part 4 — both would have been masked by speculative closure).

**How to apply:**

1. Push the task commit.
2. Schedule a ~90s wake (or equivalent — the point is the pause, not the exact interval).
3. On wake, check CI status across the OS matrix (Linux + Mac + Windows for these repos).
4. **Only then** mark `[x]` + append to `progress.md` with the commit SHA + per-OS CI times.
5. If CI fails on any OS, treat as scope expansion of the task — diagnose, fix, push the follow-up commit, restart from step 2.

Source: `~/.claude/CLAUDE.md` § Wake-on-CI pattern.

Related: [[status-report-shape]] (close-out CI block format), [[plan-md-shape]] (task narrative format includes CI times), [[verification-executable-first]].
```

**33 answer:** 

---

## 34

```
---
type: reference
status: active
captured: '2026-05-22'
updated: '2026-05-22'
slug: nas-backup
tags: [homelab, nas, unraid, backup, secondary, domain-reference]
---

# Backup NAS — secondary Unraid box

A second physical NAS box that serves as the backup target for the primary [[nas-unraid]]. Operator-confirmed 2026-05-22.

## Confirmed facts (2026-05-22)

| Attribute | Value |
|---|---|
| OS | Unraid 7.1.2 (same version as primary — deliberately kept in lockstep) |
| Role | Backup target for [[nas-unraid]] |
| Hardware | Older than primary; same disk count + parity posture |
| Web UI | `http://192.168.86.240` (when powered on) |
| KVM-over-IP / IPMI | `http://192.168.86.241` (when powered on) |
| SSH | Enabled |
| Disks | 8 array disks, dual-disk parity redundancy (matches primary) |
| File-share posture | SMB shares secured to single user `alex` with password (matches primary) |
| Power state | **Off by default**; wake-on-LAN monthly for backup runs |
| Backup cadence | Monthly |
| External exposure | None — same operator preference as primary (see [[network-topology]]) |

## Why the lockstep Unraid version

Operator keeps both boxes on the **same Unraid version** (7.1.2 currently). Rationale (inferred — operator confirms): backup-target divergence creates restore-time risk if the primary fails — different Unraid versions can have different filesystem feature support, plugin compatibility, share format. Keeping them in lockstep eliminates that class of restore failure.

**Convention**: when primary upgrades to a new Unraid version, the backup gets the same upgrade in the next monthly wake-up window. Out-of-band upgrades to the backup (e.g. emergency security patch) require careful coordination.

## What "monthly wake-up for backups" implies

The backup is **cold storage most of the time** — off, no power draw, no failure surface. Once a month, the operator powers it on (presumably wake-on-LAN), the backup runs (mirroring the primary's array — details below), then it powers off again. This is the **air-gapped-backup** posture: a backup that's only online during the actual backup window is immune to ransomware / accidental-delete events that propagate from the primary in between backups.

> **TO CONFIRM**: Wake-on-LAN mechanism — is it scheduled (cron-like trigger on the primary that WOLs the backup at a known time) or manual (operator powers it on)?
> **T
…truncated…
```

**34 answer:** 

---
