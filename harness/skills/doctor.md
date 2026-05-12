# Skill: doctor

**Purpose:** verify an installed agentic-harness is actually wired up correctly in *this* host — that the expected sub-agents, skills, slash commands, and hooks are discoverable and runnable. Companion to `templates/scripts/telemetry.sh`: telemetry answers "is the harness being used well over time?"; `doctor` answers "is it installed correctly right now?".

**Not for:** ongoing health monitoring, CI gating, or replacing `/review`. Run it after a fresh install, after a harness update, or when something feels broken.

## Modes

| Mode | What runs | Token cost | Typical runtime |
|---|---|---|---|
| default (`/doctor`) | Structural discovery only — file presence + frontmatter parse + hook-path resolution. No sub-agent dispatches. | None | <5s |
| `/doctor --live` | Default checks **plus** live sub-agent probes and skill dry-runs. This is the "does it actually work" mode. | Moderate (one dispatch per agent, one dry-run per skill) | 30–90s |
| `/doctor --live --verbose` | Same as `--live` but prints the raw agent outputs instead of pass/fail summaries. Useful when a probe fails and you need to see why. | Same as `--live` | Same as `--live` |

Default is deliberately cheap so `/doctor` can be the reflex "did my install land?" check. `--live` is opt-in because it costs tokens.

## Adapter detection

Before any checks run, `doctor` detects which adapter is installed by looking for the canonical directory layout:

| Adapter | Marker |
|---|---|
| Claude Code | `.claude/commands/` + `.claude/agents/` |
| Antigravity | `.agent/workflows/` + `.agent/skills/` |
| Gemini | `.gemini/commands/` + `.gemini/agents/` |

Multiple adapters may be present in the same project (the installer supports that). Run the full battery against each one found and report per-adapter.

If no adapter is detected, abort with `doctor: no harness adapter found in .claude/, .agent/, or .gemini/ — run install.sh first`.

## Default-mode checks (structural)

For each detected adapter, verify the expected name set is present and each file parses. The expected sets come from the same source as `scripts/check-parity.sh`:

- **Phase commands**: `bugfix, plan, release, review, setup, work`.
- **Sub-agents**: `adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer`.
- **Skills**: `dependabot-fixer, doctor, migrate-to-diataxis, ship-release`.

For each expected item:
1. The file exists at the adapter-specific path.
2. The frontmatter YAML (markdown) or top-level TOML parses cleanly.
3. **For surfaces that carry an explicit `name:` field**, the field matches the filename/dirname. Surfaces that carry `name:`: Claude Code sub-agents and skills, Antigravity skills (including sub-agents-as-skills), Gemini sub-agents. Surfaces *without* `name:` (name is implicit from filename): Claude Code phase commands, Antigravity workflows, Gemini TOML commands. Do **not** flag missing `name:` on those.

Then:
4. **State files**: `.harness/PLAN.md`, `.harness/progress.md`, `.harness/scripts/telemetry.sh` all exist.
5. **Host wiring file**: `AGENTS.md` exists at repo root. Adapter-specific overlay file exists (`CLAUDE.md` for Claude Code, `.gemini/settings.json` for Gemini pointing at `AGENTS.md`).
6. **Hooks** (Claude Code only, if `.claude/settings.json` contains a `hooks` block):
   - Every command string in `hooks[*][*].hooks[*].command` resolves to a file that exists.
   - The shell prefix matches the installer variant: bash installer produces `bash -c '...'`-style or direct shell invocations; pwsh installer produces `pwsh -File '...'` invocations. Mismatch is a fail.

## `--live` probes

Run in order. First failure stops the battery for that adapter (the rest will only produce noise if the foundation is broken).

### Probe 1: `explorer` sub-agent dispatch

Dispatch the `explorer` sub-agent with a trivial prompt that only requires filesystem access:

> *Return the absolute path of `README.md` at the repo root, and the path of `.harness/PLAN.md`. One sentence each, no commentary.*

**Pass criteria:** agent returns within 60s; output contains both `README.md` and `.harness/PLAN.md` as absolute paths; no tool-permission errors.
**Fail signals:** the sub-agent isn't visible to the host (adapter registration broken), permission denied on read (sandbox mis-wired), agent hallucinates paths without reading.

### Probe 2: `adversarial-reviewer` sub-agent dispatch

Dispatch with a deliberately-buggy snippet inline in the prompt:

> *Review this function for bugs. Report the single most important defect as a failing test or a specific file:line. Prose-only critiques are rejected.*
>
> ```python
> def divide(a, b):
>     return a / b  # no zero-check
> ```

**Pass criteria:** agent returns an executable artifact — a failing test, a `file.py:2`-style pointer, or an explicit reproduction — *not* a prose "consider adding a zero-check". This exercises the agent's output-contract enforcement.
**Fail signals:** prose-only response (means the adapter's system prompt isn't being applied), or no defect found (means the agent isn't engaging with the code).

### Probe 3: `ship-release --dry-run`

Invoke `ship-release --dry-run`. This should compute a proposed version and notes **without** tagging or pushing.

**Pass criteria:** skill prints a proposed `vX.Y.Z`, classifies the commit range, and exits cleanly without side effects. `git tag --list` is unchanged. `git status` still clean.
**Fail signals:** skill actually creates a tag (guardrail broken), skill crashes on the preconditions check, `gh auth status` failure surfaces without being caught.

### Probe 4: `migrate-to-diataxis` preview on already-migrated tree

Invoke `migrate-to-diataxis` in preview mode against the current `wiki/`. If `wiki/.diataxis` marker is present, the skill should no-op cleanly with "already migrated".

**Pass criteria:** skill detects the marker, prints the no-op message, exits without proposing moves.
**Fail signals:** skill proposes re-classifications of already-placed files (classification logic broken), or crashes reading the marker.

### Probe 5: `dependabot-fixer` "nothing matched" path

Invoke `dependabot-fixer` with no matching Dependabot PRs open. The skill should exit cleanly with "no matching PRs found", not crash or try to fix a non-existent PR.

**Pass criteria:** one-line "nothing to fix" output, exit 0.
**Fail signals:** the skill tries to check out a PR branch, or fails on `gh pr list` parsing.

### Probe 6: hook synthetic trigger (Claude Code + `--hooks`, optional)

Only runs if `.claude/settings.json` has a hooks block. Write a trivial no-op file change to a scratch file under `/tmp/` with the project's configured verify command applied manually (not through a real Write tool invocation, to avoid actually modifying the repo). Verify the command runs and exits 0 on an empty file.

**Pass criteria:** the configured verify command exits 0 on an empty file of the matching extension.
**Fail signals:** verify.sh not executable, wrong interpreter, missing dependency.

This probe is best-effort: if `verify.sh` requires project-specific tooling (a specific `npx`, `ruff`, etc.) and that tooling isn't installed, report **skip** with the reason, not **fail**.

## Output contract

```
doctor: <adapter> — <PASS|FAIL>

  structural:
    phase-commands    [OK]  6/6 present, frontmatter valid
    sub-agents        [OK]  4/4 present, frontmatter valid
    skills            [OK]  4/4 present, frontmatter valid
    state files       [OK]  PLAN.md + progress.md + telemetry.sh
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  3/3 command paths resolve

  live probes (--live):
    explorer          [OK]   2.1s  — returned 2 paths
    adversarial       [OK]   3.4s  — executable artifact returned
    ship-release      [OK]   1.8s  — proposed v0.9.0, no tag written
    migrate-diataxis  [OK]   0.9s  — no-op (marker present)
    dependabot-fixer  [OK]   1.2s  — no matching PRs
    hooks             [SKIP] ruff not installed — cannot exercise *.py case

summary: 10 OK, 0 FAIL, 1 SKIP
```

On any `FAIL`, the skill prints the specific reason under the failing row, exits non-zero, and does **not** attempt to self-repair. Fixes are the user's call.

## What `doctor` does not do

- **Does not install anything.** If a file is missing, it reports the gap and points at `install.sh`/`install.ps1`. Auto-install on top of a half-installed tree can mask misconfiguration.
- **Does not mutate state.** No writes outside `/tmp/` (hook probe scratch), no tag creation, no commits. Dry-runs and preview modes only.
- **Does not replace CI.** `scripts/check-parity.sh`, `scripts/check-references.py`, and `scripts/validate-adapters.py` are the repo-level invariants. `doctor` is per-installation.
- **Does not grade the user's customizations.** If `verify.sh` has been customized for the project, `doctor` exercises the current form — it doesn't enforce the template.

## Invocation per adapter

| Adapter | Invocation |
|---|---|
| Claude Code | `/doctor` or `/doctor --live` (skill auto-triggers on "check my harness install" / "is the harness working") |
| Antigravity | Prompt: *"Run the doctor skill"* (optionally `--live`) |
| Gemini | Reads skill from `.agents/skills/doctor/SKILL.md` (delivered by `install.sh` per the Agent Skills standard) |

## Guardrails

- **Never run `--live` without the user's explicit `--live` flag or spoken consent.** Live probes cost tokens.
- **Never write to the repo working tree.** Scratch files go under `/tmp/` or `$TMPDIR`.
- **Never invoke a skill without its dry-run / preview flag** in probe mode. The probes assert no-op semantics; a probe that tags a release would be a bug.
- **Stop at the first foundational failure.** If structural checks fail, skip `--live` probes — they'll just compound the noise.
