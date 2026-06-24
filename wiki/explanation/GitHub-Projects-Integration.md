# GitHub Projects integration

Why every phase command can file deferred-work items to a GitHub Project — and why the integration is opt-in, preview-and-ask at every write, and a silent no-op when unconfigured. The ownership mechanics are recorded in [ADR 0003](agentm-foundations-hld).

## What it's for

An operator running `/plan`, `/work`, `/review`, or `/release` should be able to offload "remember for later" items to a durable backlog without leaving the session. Three properties make that safe rather than noisy:

- **Opt-in only.** An operator who doesn't care about Projects sees zero behaviour change — there is no project until they create one at `/setup`.
- **Preview-and-ask at every write.** No `gh project item-create` ever runs without the operator first seeing the exact title and body and confirming. A `gh` call without confirmation is a contract violation.
- **Symmetric with `/bugfix` Issues.** Issues carry bugs through a full lifecycle; Projects carry "deferred work" as single items. The two surfaces are parallel, not overlapping.

The same wiring lands identically on both supported host adapters — [Claude Code and Antigravity](Compatibility) — so the behaviour travels with the operator.

## How the flow is shaped

Opt-in happens once, at `/setup`: the operator is offered a project, and on yes the agent runs the two-step ProjectsV2 dance — `gh project create` then `gh project link`. The two steps are load-bearing, because ProjectsV2 has no repo-owned form: to make a project appear under the repo you must link a user- or org-owned project to it. The rationale is [ADR 0003](agentm-foundations-hld). On success, `.harness/project.json` records `{owner, number, url, repo}`; the *absence* of that file is the signal every downstream phase uses to skip silently.

Each phase proposes from a different signal, with a soft cap that flags scope-creep rather than hard-blocking:

| Phase | What triggers a proposal | Soft reminder |
|---|---|---|
| `/plan` | Items in the plan's `## Out of scope` that read as "deferred, not rejected" | >5 proposals → rethink whether some are in-scope tasks |
| `/work` | Adjacent bugs, refactors, coverage gaps noticed while implementing — *not* task follow-ups | >3 → probably scope-creeping, not deferring |
| `/review` | Findings the operator elects to defer rather than block on | No hard cap — five real deferrals, propose five |
| `/release` | Cross-session *themes* that emerged from this release cycle | Higher bar — a theme is a pattern, not a single item |

Every phase's Projects block starts with the same graceful-skip check: `project.json` absent, `gh` unavailable, or nothing deferred this session — skip, with no prompt. The operator's opt-in is a one-time decision; phases with nothing to propose don't ask.

## A call that got reversed

An earlier draft capped proposals at "at most one per session." It was dropped in favour of the quality-bar-plus-batching rule above: capping at one forced silent misses when a session genuinely surfaced several deferrals, whereas batching them into a single preview gives the operator the same "one decision" experience without the information loss. That reversal is itself a small worked example of why the harness records its calls — the cap looked reasonable until the dogfood showed it dropping real items.

## Related

- [ADR 0003 — ProjectsV2 ownership and linking](agentm-foundations-hld) — why the create-then-link dance.
- [Configure a new project](Configure-A-New-Project) — the `/setup` flow that writes `project.json`.
- [Host adapters](Host-Adapters) — how the same wiring reaches both hosts.
