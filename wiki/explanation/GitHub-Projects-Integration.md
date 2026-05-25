# Feature: GitHub Projects integration

> [!NOTE]
> **Status:** implemented
> **Plan:** `.harness/PLAN.md` — "GitHub Projects wiring + documenter end-to-end dogfood" (tasks 2 + 3)
> **Last updated:** 2026-04-21

Every phase command (`/setup`, `/plan`, `/work`, `/review`, `/release`) can now file deferred-work items to a user- or org-owned GitHub Project linked to the repo. Opt-in at `/setup`, preview-and-ask at every `gh` call, silent graceful-skip when `.harness/project.json` is absent or `gh` is unavailable. Symmetric with the `/bugfix` Issues lifecycle that shipped in v0.8.2.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| How does a user opt in? | `/setup` step 8 — creates a ProjectsV2 project, links it to the repo, writes `.harness/project.json` |
| Where does each phase propose items? | `/plan` from the plan's `## Out of scope` section; `/work` for out-of-task-scope findings; `/review` for deferred findings; `/release` for cross-session themes |
| What if no project is configured? | Every phase silently skips — no prompt, no `gh` call |
| Canonical spec | [`harness/documentation.md`](https://github.com/alexherrero/agentm/blob/main/harness/documentation.md) §GitHub Projects + Issues |
| Ownership decision | [ADR 0003](0003-ProjectsV2-Ownership-And-Linking) |
| Related feature | [`feat-gh-issues-integration`](https://github.com/alexherrero/agentm/blob/main/.harness/features.json) — the Issues half (shipped in v0.8.2) |

## Intent

A user running `/plan`, `/work`, `/review`, or `/release` should be able to offload "remember for later" items to a durable backlog without leaving the session. The flow needs to be:

- **Opt-in only.** A user who doesn't care about Projects sees zero behavior change.
- **Preview-and-ask at every write.** No `gh project item-create` ever runs without the user seeing the title + body first and typing `y`.
- **Symmetric with `/bugfix` Issues.** Issues handle bugs with a full lifecycle; Projects handle "deferred work" as single items. The two surfaces are parallel, not overlapping.
- **Three-adapter parity.** Claude Code, Antigravity, and Gemini each expose the same behavior.

## Design

### Opt-in at `/setup`

Phase 8 of `/setup` offers: *"Create a GitHub Project for deferred-work tracking?"* On yes, the agent runs **two** `gh` calls in sequence:

```bash
gh project create --owner <owner> --title "<repo-name> backlog" --format json
gh project link <number> --owner <literal-owner> --repo <owner>/<repo>
```

The two-step dance (create then link) is load-bearing — ProjectsV2 has no repo-owned form, so to make a project appear under `github.com/<owner>/<repo>/projects` you must link a user- or org-owned project to the repo. Rationale is in [ADR 0003](0003-ProjectsV2-Ownership-And-Linking).

On success, `.harness/project.json` is written with `{owner, number, url, repo}`. Absence of the file is the signal for every downstream phase to skip silently.

### Phase-specific proposal rules

| Phase | What triggers a proposal | Batching rule | Soft cap reminder |
|---|---|---|---|
| `/plan` | Items in the plan's `## Out of scope` section that read as "deferred, not rejected" | Single batched preview at phase end | "More than 5 proposals → rethink whether some are in-scope tasks" |
| `/work` | Adjacent bugs, refactor opportunities, coverage gaps noticed while implementing — *not* task follow-ups | Single batched preview after commit | "More than 3 → probably scope-creeping, not deferring" |
| `/review` | Findings the user elects to defer rather than block on | Single batched preview at end of review report | No hard cap — "if five real deferrals, propose five" |
| `/release` | Cross-session **themes** that emerged from this release cycle's deferrals | Single batched preview at release-prep end | "Higher bar than per-phase — a theme is a pattern, not a single item" |

Earlier drafts included an "at most 1 per session" hard cap; it was dropped (see the [commit narrative](https://github.com/alexherrero/agentm/commit/dd173d6)) in favor of the quality-bar-plus-batching rule above. Rationale: capping at 1 forced silent misses when a session genuinely surfaced multiple deferrals; batching into a single user-facing preview gives the user the same "one decision" experience without the information loss.

### Graceful-skip conditions (all phases)

Every phase's Projects block starts with the same skip check:

- `.harness/project.json` absent → skip silently.
- `gh auth status` fails or `gh` not on PATH → skip silently.
- Nothing deferred this session → skip silently.

No prompt when skipping. The user's opt-in is a one-time decision at `/setup`; phases that have nothing to propose don't ask.

### Preview-and-ask contract

Per [`harness/documentation.md` §GitHub Projects + Issues](https://github.com/alexherrero/agentm/blob/main/harness/documentation.md), *every* `gh project item-create` invocation must show the exact title + body the agent intends to use, followed by an explicit yes/no prompt. A `gh` call without user confirmation is a violation. The adapter copies each carry this rule verbatim.

## Implementation

Canonical block per phase (the adapter copies reference these paths rather than duplicating the contract):

| Phase | File | Block |
|---|---|---|
| `/setup` | [`harness/phases/01-setup.md#L140-L191`](https://github.com/alexherrero/agentm/blob/main/harness/phases/01-setup.md#L140-L191) | §8 "Offer GitHub Project creation" — two-step create + link flow |
| `/plan` | [`harness/phases/02-plan.md#L254-L276`](https://github.com/alexherrero/agentm/blob/main/harness/phases/02-plan.md#L254-L276) | §7 "Offer deferred items to the GitHub Project" |
| `/work` | [`harness/phases/03-work.md#L241-L263`](https://github.com/alexherrero/agentm/blob/main/harness/phases/03-work.md#L241-L263) | §10 "Offer deferred items to the GitHub Project" |
| `/review` | [`harness/phases/04-review.md#L234-L254`](https://github.com/alexherrero/agentm/blob/main/harness/phases/04-review.md#L234-L254) | §8 "Offer deferred findings to the GitHub Project" |
| `/release` | [`harness/phases/05-release.md#L213-L233`](https://github.com/alexherrero/agentm/blob/main/harness/phases/05-release.md#L213-L233) | §8 "Offer next-release themes to the GitHub Project" |

Adapter parity (all three adapters carry the wiring for all five phases — 15 adapter files touched):

- [`adapters/claude-code/commands/{setup,plan,work,review,release}.md`](https://github.com/alexherrero/agentm/tree/main/adapters/claude-code/commands)
- [`adapters/antigravity/workflows/{setup,plan,work,review,release}.md`](https://github.com/alexherrero/agentm/tree/main/adapters/antigravity/workflows)
- [`adapters/gemini/commands/{setup,plan,work,review,release}.toml`](https://github.com/alexherrero/agentm/tree/main/adapters/gemini/commands)

Schema update for `.harness/project.json` (added `repo` field) in [`harness/documentation.md`](https://github.com/alexherrero/agentm/blob/main/harness/documentation.md).

Commits in range `801dbd7^..HEAD`:

| SHA | Subject |
|---|---|
| `801dbd7` | `feat: wire gh project item-create offer into /plan /work /review /release` |
| `dd173d6` | `fix: drop "at most 1 per session" cap on Project-item proposals — quality bar + batching instead` |
| `d195d4c` | `fix: ProjectsV2 create+link flow so projects appear under the repo` |
| `068d9e7` | `docs: refresh README against v0.8.2 — implement task 4 of the Projects+documenter plan` |

## Notes

- **Dogfood verification pending.** Part A of task 3 shipped — this repo's own project #2 ("agentm backlog") was created and linked. Part B (observe an offer-accept or offer-decline cycle in a real phase session) is the gate for flipping `feat-gh-projects-integration.passes` to `true` in `features.json`; see task 5 of the plan.
- **Project #1 was deleted mid-dogfood.** The original attempt at task 3 created a user-scoped project that didn't appear under the repo — which is the surprise that surfaced the ownership-and-linking decision documented in [ADR 0003](0003-ProjectsV2-Ownership-And-Linking). Project #2 is the corrected shape.
- **The `repo` field in `project.json` is load-bearing for documentation, not runtime.** Per-phase wiring only reads `owner` and `number`. The `repo` field records the linkage so dogfood-freshness checks and future `--update` re-verification can confirm the project is still linked.
- **Proposal heuristic is soft.** "Scan for deferred signals" is fuzzy by design; the two mitigations are the mandatory preview-and-ask (a noisy proposal the user declines is cheap) and the per-phase soft caps in the table above.
