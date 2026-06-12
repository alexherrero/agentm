---
title: <Replace with descriptive title>
status: draft
visibility: confidential
author: <Replace with your name>
contributors: []
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
last_major_revision: <YYYY-MM-DD>
prd: <URL or leave empty>
project: <GitHub Project URL or leave empty>
---

<!--
  crickets design-doc template (v0.1.0). Implements the locked
  10-section structure from PLAN.archive (plan #6 / locked design call #2).

  Each section below has:
    - An italic *prompt* at the top describing what goes here.
    - An HTML comment with deeper guidance + "what N/A looks like".

  The `/design author` skill walks you through filling this out section
  by section. Status transitions: draft → review → final → launched.
  The skill enforces the lifecycle; you don't edit `status:` by hand.

  Status field semantics:
    draft     — authoring in progress; not yet submitted for review.
    review    — author has signaled readiness; awaiting human approval.
    final     — human-approved; HARD GATE — `/design translate` and
                `/design sequence` only run on Status: final docs.
    launched  — all queued plans (one per part) have shipped via the
                harness `/work` + `/release` flow. Set automatically by
                harness `/release` when the last part's PLAN.md hits
                Status: done.

  Visibility field semantics:
    confidential — doc lives at `.harness/designs/<slug>.md` (gitignored,
                   machine-local; not committed to a public repo).
    published    — doc lives at `wiki/explanation/designs/<slug>.md`
                   (committed; surfaces in wiki/Home.md + _Sidebar.md as
                   the canonical "Why we built X" entry point).
-->

# <Replace with title>

## Context

### Objective

*What problem does this solve, and why now? 3–4 plain sentences, max.*

<!--
  The "why" of the doc. Reader-grabbing — anyone landing on this design
  via wiki/Home.md should know in 30 seconds whether this is relevant
  to them.

  Operator convention (2026-06-09): hard cap 3–4 sentences. Plain
  language, no jargon — readable by someone outside the project.
  If it needs more room, the extra belongs in Background.

  N/A is NOT appropriate here. Every design needs an objective.
-->

### Background

*Context the reader needs to understand the rest of the doc. History, related work, current state. Max 3 paragraphs.*

<!--
  What led to this design? What existing systems / decisions / pain
  points motivated it? Link to ADRs, prior designs, or external refs.

  Operator convention (2026-06-09): max 3 coherent paragraphs, not a
  bullet pile. Cover: (1) why this is needed; (2) the environment /
  constraints that drive the decisions (where it runs, what it must
  coexist with); (3) the cost/effort realities that shape scope.

  N/A acceptable if the design is fully greenfield with no prior work
  to reference; explicitly say "N/A: fully greenfield project."
-->

## Design

### Overview

*The shape of the design in 1-3 paragraphs — what it is at a high level.*

<!--
  Reader's first orientation to the design itself. After Context/Objective
  this is the "here's what we're building" section. Save details for
  Detailed Design.

  Operator convention (2026-06-09): human-readable, plain language, the
  operator's voice. A reader who doesn't know the codebase should get
  the shape from this section alone — no internal slugs/jargon without
  a one-phrase gloss.

  No N/A — every design needs an overview.
-->

### Infrastructure

*The runtime / deployment / dependency shape — what runs where.*

<!--
  Operator convention (2026-06-09): platform-first ordering —
  (1) name the platform/framework it runs on; (2) a chart of the
  jobs/components (names + what runs in each); (3) a chart of when
  things run (triggers: push, PR, schedule, local); (4) the guarantees
  each piece provides. Deep mechanics belong in Detailed Design.

  What new infrastructure is needed? What existing infra is reused?
  Diagrams (ASCII or images) welcome.

  N/A appropriate for pure-library / pure-content designs that don't
  add infrastructure; explicitly say "N/A: no new infra; reuses
  existing X."
-->

### Detailed Design

*The full design at the level a reader needs to evaluate it. Probably the longest section.*

<!--
  This is where the actual technical content lives. Subsections per
  component / phase / module are fine. The `/design translate` skill
  uses the structure here to propose part splits (one part per
  top-level subsection by default).

  No N/A — every design needs detail here.
-->

## Alternatives Considered

*What other approaches did you consider? Why did you reject each?*

<!--
  At minimum one alternative + rationale for why you didn't pick it.

  Common alternatives to capture: "do nothing", "use library X instead",
  "refactor existing Y rather than add Z".

  N/A acceptable only if the design is so narrow there were no real
  alternatives — explicitly say "N/A: only one viable approach
  (justify in one sentence)."
-->

## Dependencies

*What does this design depend on? Internal services, libraries, teams, other designs.*

<!--
  Both technical (libraries, APIs) and organizational (teams, approvals).

  N/A appropriate for fully self-contained changes; explicitly say
  "N/A: no external dependencies."
-->

## Migrations

*Data migrations, schema changes, behavioral migrations, deprecations.*

<!--
  Any migration step a deployer must take. Include rollback compatibility.

  N/A appropriate for net-new code with no existing state; explicitly
  say "N/A: no existing state to migrate."
-->

## Technical Debt & Risks

*Known compromises, hacks, fragile points, and what could go wrong.*

<!--
  Honesty section. What did you trade off? What worries you?

  N/A INAPPROPRIATE — every design has at least one risk; if you can't
  name one, the design isn't honest enough yet.
-->

## Quality Attributes

*Per-attribute analysis. Walk all 11 attributes consciously; **include only the ones that are real** for this design.*

<!--
  The `/design author` skill prompts for each of the 11 sub-attrs —
  the WALK is mandatory (forcing conscious decisions catches ops blind
  spots early), but the DOC isn't a form.

  Operator convention (2026-06-09): attributes that are N/A or
  low-relevance are OMITTED from the final doc entirely — don't ship
  a list of "N/A: not applicable" stubs. Keep a sub-section only when
  there's something real to say. (Pre-2026-06-09 docs carry the old
  all-11-with-N/A shape; leave them unless revised for other reasons.)

  For anything CI/automation-touching, check supply-chain hardening
  under Security (e.g. pin third-party actions by SHA, not tag).
-->

### Security

*Threat model, attack surface, sensitive data handling.*

### Reliability

*Failure modes, redundancy, graceful degradation, error budgets.*

### Data Integrity

*Consistency guarantees, corruption resistance, recovery procedures.*

### Privacy

*PII handling, data minimization, consent, retention policy.*

### Scalability

*Growth assumptions, bottlenecks, scaling strategy.*

### Latency

*Performance budget, hot paths, optimization plans.*

### Abuse

*Rate limiting, anti-spam, anti-fraud, malicious-input handling.*

### Accessibility

*WCAG compliance, screen-reader support, keyboard navigation, color contrast.*

### Testability

*Test strategy, isolation, mockability, deterministic verification.*

### Internationalization & Localization

*Locale support, text externalization, RTL, date/number formatting.*

### Compliance

*Regulatory requirements (GDPR, HIPAA, SOC2, etc.), audit trail.*

## Project management

<!--
  Operator convention (2026-06-09): for a SHIPPED system (a codification
  design, or any design revised after launch), Work estimates is omitted —
  keep only Documentation Plan + Launch Plans in their slimmed forms below.
-->

### Work estimates

*Rough sizing per major piece. The `/design sequence` skill uses this to weight per-part PLAN.md tasks.*

<!--
  Per-component or per-phase estimates. S/M/L sizing is fine; calendar
  estimates if you have them. Omit this sub-section entirely for
  shipped/codified systems.
-->

### Documentation Plan

*What docs will ship with this design? User-facing, ops, ADRs.*

<!--
  Coordinates with crickets' `wiki-maintenance:documenter` sub-agent. List the pages
  that will get created/updated under `wiki/`.

  Operator convention (2026-06-09): list ALL wiki pages that document
  the system — not just the new ones this design ships.
-->

### Launch Plans

*Rollout strategy. Feature flags, phased rollouts, beta groups.*

<!--
  How does this go live? All-at-once is acceptable for small changes;
  larger changes typically phased.

  Operator convention (2026-06-09): if the launch date has already
  passed, this section is just the date(s) it launched — no plan prose.
-->

## Operations

<!--
  Operator convention (2026-06-09): same rule as Quality Attributes —
  walk all four sub-sections, but OMIT any that are N/A or low-relevance
  from the final doc. Where behavior depends on CI/automation inputs or
  outputs, say explicitly that it's the agent (LLM) doing the watching /
  reading / reacting.
-->

### SLAs

*Service-level expectations. Uptime, response time, error budgets.*

<!--
  N/A appropriate for internal tooling with no SLA exposure — omit the
  sub-section in that case.
-->

### Monitoring and Alerting

*What gets monitored, what triggers alerts, who responds.*

<!--
  Metrics, dashboards, alert thresholds, on-call routing.
-->

### Logging Plan

*What gets logged, retention, structure (structured vs. free-text).*

<!--
  Useful for audit + debugging. Include log levels + retention policy.
-->

### Rollback Strategy

*How do we back out if this design fails in production?*

<!--
  Reversibility of each change. Feature flags, schema additivity, data
  rollback procedures. "We can't roll back" is an acceptable answer
  but flag it loudly.
-->

## Document History

*Major revisions only. Git history covers per-commit changes. **One row per day**, max.*

<!--
  Entries chronological. Format:
    YYYY-MM-DD: <one-line description of major change> (Status: <new-status>)

  Initial draft entry auto-populated by `/design author` on creation.
  Mid-execution design changes append entries here — that's how the
  skill knows which parts to re-translate.

  Operator convention (2026-06-09): multiple entries on the same day are
  CONSOLIDATED into a single row telling that day's whole story (the row's
  Status = the day's end state). Append freely while working; consolidate
  before the doc is presented/committed.
-->

| Date | Change | Status |
|---|---|---|
| <YYYY-MM-DD> | Initial draft created via `/design author`. | draft |
