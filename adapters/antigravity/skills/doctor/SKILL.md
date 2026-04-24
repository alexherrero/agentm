---
name: doctor
description: Verify the agentic-harness install in this Antigravity project is correctly wired up. Trigger when the user says "check my harness install", "is the harness working", "run doctor". Default mode is structural only (no tokens, <5s) — checks that expected workflows, sub-agents (delivered under skills/), skills, and state files are present and parseable. The --live flag adds real sub-agent dispatches and skill dry-runs to prove end-to-end wiring (~30–90s, moderate token cost). Never installs or mutates state; reports gaps and points at install.sh.
---

You are running the `doctor` skill. Full canonical spec: `../../../../harness/skills/doctor.md` in the agentic-harness repo. The summary below is the operational version.

## Input handling

- **No argument** → default mode: structural discovery only.
- **`--live`** → default checks plus live probes.
- **`--live --verbose`** → include raw probe output.

## Adapter detection

This is the Antigravity adapter. Expect:
- `.agent/workflows/*.md` (phase commands)
- `.agent/skills/*/SKILL.md` (sub-agents *and* skills — Antigravity has no separate sub-agent primitive)
- `.agent/rules/harness.md` (always-on rules file)

If `.agent/` is missing, abort: `doctor: no Antigravity install detected — run install.sh /path/to/project`.

## Default-mode checks

Expected name sets:

| Surface | Expected |
|---|---|
| `.agent/workflows/*.md` | `bugfix, plan, release, review, setup, work` |
| `.agent/skills/*/` | sub-agents (`adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer`) **plus** shared skills (`dependabot-fixer, doctor, migrate-to-diataxis, ship-release`) |
| `.agent/rules/*.md` | exactly one file named `harness` |

For each expected file:
1. Exists at the right path.
2. Frontmatter YAML parses cleanly.
3. `name:` field matches filename/dirname.

Then:
4. `.harness/PLAN.md`, `.harness/progress.md`, `.harness/scripts/telemetry.sh` all exist.
5. `AGENTS.md` exists at repo root.

Report a pass/fail table. Exit here unless `--live` was passed.

## `--live` probes

Run in order. Stop at first foundational failure.

### 1. `explorer` dispatch

Dispatch `explorer` with:
> *Return the absolute path of `README.md` at the repo root, and the path of `.harness/PLAN.md`. One sentence each, no commentary.*

Pass: returns both absolute paths within 60s.

### 2. `adversarial-reviewer` dispatch

Dispatch with the inline buggy snippet (see canonical spec) — expect an executable artifact, not prose.

### 3. `ship-release --dry-run`

Invoke the shared skill with `--dry-run`. Pass: proposed version printed, no tag written, tree still clean.

### 4. `migrate-to-diataxis` preview

Invoke in preview mode. If `wiki/.diataxis` is present, expect a no-op.

### 5. `dependabot-fixer` no-match path

Pass: one-line "no matching PRs", exit 0.

## Output contract

Same pass/fail table format as the canonical spec. Hook probe is skipped on Antigravity (hooks are Claude Code-specific).

## Guardrails

- Never run `--live` probes without an explicit `--live` flag or spoken consent.
- Never write to the repo working tree.
- Never invoke a skill without its dry-run / preview flag in probe mode.
- Stop at the first foundational failure.
