# Auto-detect and auto-configure

Why the first conversation in a repo the harness hasn't seen configures *itself* instead of demanding a manual setup script — and why the result lands in `project.json`. For the operator steps, see [Configure a new project](Configure-A-New-Project); for the rule list and the schema, see [Detection rules](Detection-Rules) and [Project config](Project-Config).

## What it replaced

Before this work, opening a fresh repo meant remembering to run a manual setup script to tell the harness which skills and hooks the project should use. The friction was real: most repos never got configured, so per-project enablement never had teeth. The promise of the device-wide install arc was that the harness should configure itself on first contact — and this closes it.

The experience now: open an unconfigured code project and a single quiet line offers to set it up. Say "configure this project" (or run `/setup --detect`) and you get a scannable proposal — every skill and hook, each with one plain-language reason it's relevant to *this* repo — and a three-way choice: take it all, customize, or skip. Approve, and a durable enablement block lands in `project.json` so every later phase can resolve this repo's shape without asking again. The operator stays in control; the harness just removes the "where do I start" tax.

## How the flow is shaped

Three cooperating pieces, deliberately separated so the interactive part stays agent-driven and the deterministic part stays testable:

```
SessionStart hook ──nudge──▶ operator says "configure" / runs /setup --detect
   (one line, non-interactive)            │
                                          ▼
                          detect_project.detect(cwd)  ── deterministic, no I/O
                                          │  ProposedConfig (verdict: propose | bypass)
                                          ▼
                          render_text() ──▶ a/b/c approval (agent renders verbatim)
                                          │
                                          ▼
                          project_config.register(cwd) ── writes project.json + repo_registry
```

The load-bearing calls, and why each is what it is:

- **Enablement config lives in `project.json`, not `features.json`.** `features.json` is the governed verification ledger (the `passes: true|false` contract); enablement is a different concern with a different lifecycle. Co-mingling them would let a registration write touch the ledger. The block is *additive* alongside the existing `vault_project` / `github` / `env` keys.
- **The nudge extends the existing SessionStart hook, not a new one.** It rides the hook's "nothing resolved" else-branch, preserving the original inject-vault-paths behaviour rather than duplicating it.
- **The hook only nudges; scan/propose/approve/write is agent-driven.** Hooks are non-interactive — they emit one line into context. All the gating logic lives in `project_config.py` so the decision stays unit-testable and the hook stays a thin emitter.
- **Detection augments `/setup`; it doesn't replace it.** The scan is the new §0 at the front of `/setup`, ahead of the inventory and interview.
- **Default-all-enabled.** Detection never gates which skills or hooks are present — it surfaces *rationale* so the operator can make an informed opt-out. Opt-outs are recorded in `operator_overrides`.
- **The permeable boundary.** Detection *proposes*; the operator approves or edits; nothing is ever written to the vault silently.

## What stays a known risk

**Nudge fatigue** is the main one, mitigated by keeping the nudge to a single line and honouring the `.agentm-no-register` escape hatch — drop that marker for a one-time scratch session and the hook stays silent. A `.envrc` (direnv) file is a known false positive; the operator declines it at approval, and the decline is recorded so a future re-detect won't re-suggest it. If the vault is unavailable, detection still renders the proposal but skips the persist, noting it.

Deliberately deferred (do not treat as shipped): the pluggable rule API, the `/setup --redetect` diff flow, per-project rule disabling, the project-type taxonomy, and monorepo sub-project registration.

## Related

- [Configure a new project](Configure-A-New-Project) — the operator recipe.
- [Detection rules](Detection-Rules) — the built-in rules and what each attaches a rationale to.
- [Project config](Project-Config) — the enablement-block schema.
- [Orchestration and auto-detection](Orchestration-And-Auto-Detection) — where this sits in the architecture.
