---
name: doctor
description: Verify the agentic-harness install in this project is correctly wired up. Trigger when the user says "check my harness install", "is the harness working", "run doctor", or invokes /doctor. Default mode is structural only (no tokens, <5s) — checks that expected phase commands, sub-agents, skills, state files, and hooks are present and parseable. The --live flag adds real sub-agent dispatches and skill dry-runs to prove end-to-end wiring (~30–90s, moderate token cost). Never installs or mutates state; reports gaps and points at install.sh.
---

You are running the `doctor` skill. Full canonical spec: `harness/skills/doctor.md` in the agentic-harness repo. The summary below is the operational version.

## Input handling

- **No argument** → default mode: structural discovery only.
- **`--live`** → default checks plus live probes (one dispatch per sub-agent, one dry-run per skill).
- **`--live --verbose`** → same as `--live` but print raw probe output on each row.

## Adapter detection

This is the Claude Code adapter, so expect:
- `.claude/commands/*.md` (phase commands)
- `.claude/agents/*.md` (sub-agents)
- `.claude/skills/*/SKILL.md` (skills)
- `.claude/settings.json` (hooks, if `install.sh --hooks` was used)

If `.claude/` is missing, abort: `doctor: no Claude Code install detected — run install.sh /path/to/project`.

## Default-mode checks

Expected name sets (must match exactly — extras and missings both fail):

| Surface | Expected |
|---|---|
| `.claude/commands/*.md` | `bugfix, plan, release, review, setup, work` |
| `.claude/agents/*.md` | `adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer` |
| `.claude/skills/*/` | `dependabot-fixer, doctor, migrate-to-diataxis, ship-release` |

For each expected file:
1. Exists at the right path.
2. Frontmatter YAML parses cleanly (no trailing-tab or quote issues).
3. `name:` field matches filename/dirname.

Then:
4. `.harness/PLAN.md`, `.harness/progress.md`, `.harness/scripts/telemetry.sh` all exist.
5. `AGENTS.md` + `CLAUDE.md` exist at repo root.
6. If `.claude/settings.json` has a `hooks` block: every `command` string resolves to a file that exists; bash installer produced bash-shell commands (not pwsh).

Report a pass/fail table. Exit here unless `--live` was passed.

## `--live` probes

Run in order. Stop at first foundational failure — structural breakage makes later probes meaningless.

### 1. `explorer` dispatch

Dispatch `explorer` with:
> *Return the absolute path of `README.md` at the repo root, and the path of `.harness/PLAN.md`. One sentence each, no commentary.*

Pass: returns both absolute paths within 60s.

### 2. `adversarial-reviewer` dispatch

Dispatch with this inline prompt:

> *Review this function for bugs. Report the single most important defect as a failing test or a specific file:line. Prose-only critiques are rejected.*
>
> ````python
> def divide(a, b):
>     return a / b  # no zero-check
> ````

Pass: returns an executable artifact (failing test or file:line pointer), not prose.

### 3. `ship-release --dry-run`

Invoke the `ship-release` skill with `--dry-run`.

Pass: prints a proposed `vX.Y.Z` and notes; `git tag --list` unchanged; `git status` still clean.

### 4. `migrate-to-diataxis` preview

Invoke `migrate-to-diataxis` in preview mode. If `wiki/.diataxis` is present, expect a no-op.

Pass: detects the marker, prints the no-op line, proposes no moves.

### 5. `dependabot-fixer` no-match path

Invoke with no matching Dependabot PRs open.

Pass: one-line "no matching PRs", exit 0.

### 6. Hook synthetic trigger (optional)

Only if `.claude/settings.json` has hooks. Exercise the project's `verify.sh` against an empty scratch file under `$TMPDIR` with a matching extension. Report **skip** (not fail) if project tooling (`ruff`, `npx`, etc.) is missing.

Pass: verify command exits 0 on the empty file.

## Output contract

```
doctor: claude-code — <PASS|FAIL>

  structural:
    phase-commands    [OK]  6/6 present, frontmatter valid
    sub-agents        [OK]  4/4 present, frontmatter valid
    skills            [OK]  4/4 present, frontmatter valid
    state files       [OK]  PLAN.md + progress.md + telemetry.sh
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  3/3 command paths resolve

  live probes (--live):
    explorer          [OK]   2.1s
    adversarial       [OK]   3.4s
    ship-release      [OK]   1.8s  — proposed v0.9.0, no tag written
    migrate-diataxis  [OK]   0.9s  — no-op (marker present)
    dependabot-fixer  [OK]   1.2s
    hooks             [SKIP] ruff not installed

summary: 10 OK, 0 FAIL, 1 SKIP
```

On any `FAIL`, print the specific reason under the failing row, exit non-zero, do not auto-repair.

## Guardrails

- Never run `--live` probes without an explicit `--live` flag or spoken user consent.
- Never write to the repo working tree. Scratch files go under `$TMPDIR`.
- Never invoke a skill without its dry-run / preview flag in probe mode.
- Stop at the first foundational failure; don't compound noise.
