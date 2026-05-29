# Feature: auto-detect + auto-configure on first session

> [!NOTE]
> **Status:** implemented
> **Plan:** `.harness/PLAN.md` tasks 1 (detection engine), 2 (`project.json` enablement block), 3 (`/setup` detect flow), 4 (SessionStart nudge).
> **Last updated:** 2026-05-29

The first conversation in a repo the harness hasn't seen configures itself instead of requiring a manual setup script. A quiet SessionStart nudge offers to configure the repo; on request, a deterministic engine scans it against 10 rules, proposes a default-all-enabled config with a per-skill/per-hook rationale, and on approval persists the enablement block to `project.json`. This closes the global-install arc's last promise.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What triggers it? | A one-line SessionStart nudge in unconfigured code projects (silent for configured / `R-harness`-bypassed / `.agentm-no-register` repos). |
| Who scans? | The detection engine — see [Detection rules](../reference/Detection-Rules.md). |
| Where does config land? | The `project.json` enablement block — see [Project config](../reference/Project-Config.md). |
| How do I run it? | See [Configure a new project](../how-to/Configure-A-New-Project.md). |

## Intent

Before V4 #32, opening a fresh repo meant remembering to run a manual setup script (the never-shipped `setup-project.sh`) to tell the harness what skills and hooks the project should use. The friction was real: most repos never got configured, so per-project enablement never had teeth. The promise of the global-install arc was that the harness should configure *itself* on first contact.

The user-facing experience: open an unconfigured code project and a single, quiet line offers to set it up. Say "configure this project" (or run `/setup --detect`) and you get a scannable proposal — every skill and hook, each with one plain-language reason for why it's relevant to *this* repo — and a three-way choice: take it all, customize, or skip. Approve, and a durable enablement block lands in `project.json` so every later phase can resolve this repo's `{slug, type, enabled skills/hooks}` without asking again. The operator stays in control; the harness just removes the "where do I start" tax.

## Design

The flow is three cooperating pieces, deliberately separated so the interactive part stays agent-driven and the deterministic part stays testable.

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

The load-bearing design calls, and why each is what it is:

- **DC-1 — enablement config lives in `project.json`, not `features.json`.** `features.json` is the governed verification ledger (the `passes: true|false` contract). Enablement is a different concern with a different lifecycle; co-mingling them would let a registration write touch the ledger. The block is *additive* on `project.json` alongside the existing `vault_project` / `github` / `env` keys.
- **DC-2 — the nudge extends `harness-context-session-start`, not a new 11th hook.** It rides the hook's existing "nothing resolved" else-branch. The original inject-vault-paths behavior is preserved and re-tested rather than duplicated.
- **DC-3 — the hook only nudges; the scan/propose/approve/write flow is agent-driven.** Hooks are non-interactive (stdout into context only). All the gating logic lives in `project_config.py should-nudge` so the hook stays a thin emitter and the decision is unit-testable.
- **DC-5 — #32 augments `/setup`; it doesn't replace it.** Detection is the new §0 at the front of `/setup`, ahead of the inventory + interview.
- **DC-6 — `type` defaults to `"coding"`; the taxonomy stays V5.** `R-non-coding` ships as a stub that never matches.
- **DC-7 — default-all-enabled.** Detection surfaces rationale; it never gates which skills/hooks are present. A matched rule overlays *why* a target is relevant so the operator can make an informed opt-out; opt-outs are recorded in `operator_overrides`.
- **A3 permeable boundary.** Detection *proposes*; the operator approves or edits; nothing is written to the vault silently.

## Implementation

| Concern | Where | Notes |
|---|---|---|
| Detection engine | [`scripts/detect_project.py`](https://github.com/alexherrero/agentm/blob/main/scripts/detect_project.py) — `detect()` (L320), `RULES` registry (L302) | Side-effect-free scan; `R-harness` runs first so a `bypass` short-circuits before the rest. |
| Proposal data shapes | `detect_project.py` — `RuleMatch` (L98), `TargetState` (L113), `ProposedConfig` (L121) | `ProposedConfig.to_dict()` serializes the `--format json` form. |
| Default-all-enabled baseline | `detect_project.py` — `ENABLEABLE_SKILLS` (L55), `ENABLEABLE_HOOKS` (L65) | Every target starts enabled; matched rules overlay rationale + `rule_id`. |
| Operator-facing block | `detect_project.py` — `render_text()` (L381) | Renders the a/b/c approval prompt for `--format text`. |
| Enablement block builder | [`scripts/project_config.py`](https://github.com/alexherrero/agentm/blob/main/scripts/project_config.py) — `build_enablement_block()` (L74) | Raises on a `bypass` verdict — no config for a harness repo. |
| Non-clobbering merge | `project_config.py` — `merge_enablement()` (L98) | Overwrites only `_ENABLEMENT_KEYS` (L40); preserves `vault_project` / `github` / `env`. |
| Opt-out recording | `project_config.py` — `apply_override()` (L111) | Flips `enabled→False`, sets `operator_action`, appends to `operator_overrides`. |
| Registered check | `project_config.py` — `is_registered()` (L151) | Non-empty `skills` block OR a `repo_registry` entry. |
| Vault-aware write | `project_config.py` — `write_config()` (L189) routes through `harness_memory.write_state_file` | `.project-mode`-aware so a local-mode project reads and writes the same file. |
| End-to-end register | `project_config.py` — `register()` (L203) | Detect → build → merge → write → `repo_registry.register_repo`. |
| `/setup` §0 flow | [`harness/phases/01-setup.md` §0](https://github.com/alexherrero/agentm/blob/main/harness/phases/01-setup.md) | Mirrored as constraint 0 in the Claude Code / Antigravity / Gemini adapters. |
| Nudge branch | [`harness-context-session-start.sh`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/harness-context-session-start/harness-context-session-start.sh) + `.ps1` | Else-branch delegates to `project_config.py should-nudge` (L254); emits one line on exit 0. |

## Notes

- **Nudge fatigue** is the main known risk, mitigated by keeping it a single line and honoring the `.agentm-no-register` escape — drop that marker for a one-time scratch session and the hook stays silent.
- **`.envrc` (direnv) is a known `R-pii` false positive.** The operator declines it at approval; the decline is recorded in `operator_overrides` so a future re-detect won't re-suggest it.
- **Graceful degradation:** if the vault is unavailable, detection still renders the proposal but `register` can't persist — `/setup` surfaces the proposal and skips the write, noting it. On a pre-v4.8.0 install (`detect_project.py` absent), `/setup` skips §0 entirely.
- **Deferred — do not treat as shipped:** the pluggable `~/.config/agentm/detection-rules.d/` rule API; the `/setup --redetect` diff flow (`last_redetect_at` stays `null`); per-project `detection-rules-disabled.json`; the project type taxonomy (build/vacation/research — V5); monorepo sub-project registration.

## Related

- [Configure a new project](../how-to/Configure-A-New-Project.md) — the operator recipe.
- [Detection rules](../reference/Detection-Rules.md) — the 10 built-in rules.
- [Project config](../reference/Project-Config.md) — the enablement-block schema.
- [How the pieces fit](How-The-Pieces-Fit) — where this sits in the phase/adapter/template model.
