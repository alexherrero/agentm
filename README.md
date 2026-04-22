# agentic-harness

A small, opinionated harness for doing production-quality engineering with AI coding agents (Claude Code, Antigravity, Codex, Gemini, and tools that read `AGENTS.md`).

Not a 150-agent supermarket. Six phase-gated slash commands, three sub-agents (`explorer`, `adversarial-reviewer`, `documenter`), deterministic verification, on-disk state, and a narrative `wiki/` that syncs to the GitHub Wiki. Designed to be installed into any project in one command.

## Principles (the short version)

1. **Phase-gated workflow.** `setup → plan → work → review → release`. Hard boundaries.
2. **State lives on disk, not in context.** `.harness/PLAN.md`, `features.json`, `progress.md`, git.
3. **Single-threaded for coherence, fan-out only for read-only breadth.** Parallel implementers cause merge chaos; parallel readers are fine.
4. **Deterministic gates before LLM judgment.** Typecheck → lint → test → build, then optional critic.
5. **Adversarial review with "assume bugs" framing.** Neutral reviewers rubber-stamp; adversarial ones find things.
6. **Re-audit the harness on every model bump.** Scaffolding that was load-bearing last quarter often isn't anymore.

Full reasoning in [harness/principles.md](harness/principles.md).

## Install into a project

```bash
# First install:
/path/to/agentic-harness/install.sh [--hooks] /path/to/your-project

# Refresh harness-authored files to the current version (leaves your edits alone):
/path/to/agentic-harness/install.sh --update /path/to/your-project
```

On **Windows** (PowerShell 7+), the `install.ps1` twin is semantically equivalent:

```powershell
# First install:
pwsh -NoProfile -File C:\path\to\agentic-harness\install.ps1 [-Hooks] C:\path\to\your-project

# Refresh harness-authored files:
pwsh -NoProfile -File C:\path\to\agentic-harness\install.ps1 -Update C:\path\to\your-project
```

Either installer drops in both `.sh` and `.ps1` versions of helper scripts (so mixed-OS teams are covered regardless of who ran the installer). The `--hooks` / `-Hooks` flag registers the host-appropriate hook commands into `.claude/settings.json` — `bash` on POSIX, `pwsh -File` on Windows.

This drops in:
- `.harness/` — per-project state (PLAN.md, features.json, progress.md, init.sh, known-migrations.md) and `scripts/` (e.g. `cross-review.sh` for cross-model review via Gemini)
- `.claude/commands/` + `.claude/agents/` + `.claude/skills/` — slash commands, sub-agents (`explorer`, `adversarial-reviewer`, `documenter`), and skills for Claude Code
- `wiki/` + `.github/workflows/wiki-sync.yml` — narrative documentation scaffold (four subdirs: `development/`, `operational/`, `design/`, `architecture/`) maintained by the `documenter` sub-agent at phase boundaries and mirrored to the GitHub Wiki on every push. Full convention in [harness/documentation.md](harness/documentation.md).
- `AGENTS.md` + `CLAUDE.md` — agent entry points (Antigravity, Cursor, Codex, Claude Code, Gemini)

With `--hooks` / `-Hooks`:
- `.harness/verify.sh` + `.harness/verify.ps1` — per-project verification script (edit to uncomment checks for your stack)
- `.harness/hooks/precompact.{sh,ps1}` — appends a marker to `progress.md` before compaction wipes context
- `.harness/hooks/session-start-compact.{sh,ps1}` — re-anchors Claude on the state files when a session resumes from compact
- `.claude/settings.json` — `PostToolUse`, `PreCompact`, and `SessionStart(compact)` hooks. Merges safely into existing settings. Canonical hook JSON lives in `templates/hooks/settings-fragment-{bash,pwsh}.json`.

See [harness/hooks.md](harness/hooks.md) for the full design. POSIX `--hooks` requires `jq`; Windows `-Hooks` uses PowerShell-native JSON. Idempotent — safe to re-run.

**What `--update` does:** refreshes harness-authored files (commands, agents, skills, hooks, helper scripts) to the current harness version. Leaves user-authored files alone (`PLAN.md`, `progress.md`, `features.json`, `init.sh`, `verify.sh`, `known-migrations.md`, `AGENTS.md`, `CLAUDE.md`). Writes `.harness/.version` so subsequent runs can show a version delta.

## Phases

| Command | Purpose |
|---|---|
| `/setup` | First-time project init: scaffold, `init.sh`, feature list |
| `/plan` | Turn a brief into `.harness/PLAN.md` — tasks with pass/fail criteria |
| `/work` | Execute one task from the plan; update progress; stop |
| `/review` | Adversarial critique of the change — must produce executable artifact |
| `/release` | Pre-merge gate: clean tree, verification passes, changelog |
| `/bugfix` | Report → Analyze → Fix → Verify pipeline (replaces `/work` for bugs). Maintains a GitHub Issue as the public posterity record across all four phases — preview-and-ask on every `gh issue *` call, graceful-skip if `gh` is unavailable. |

## Skills

Background utilities that auto-trigger or run on a schedule, separate from the phase commands.

| Skill | Triggers when |
|---|---|
| `dependabot-fixer` | A Dependabot PR has red CI. Reads failing logs + upstream CHANGELOG, applies a bounded fix loop, pushes commits to the Dependabot branch, comments residual risks. Never merges. ([spec](harness/skills/dependabot-fixer.md)) |
| `ship-release` | A feature just went green end-to-end (`/release` clean, `features.json` entry flipped to `passes: true`). Computes the next semver from the commit range, writes release notes from CHANGELOG + commit log, tags, pushes, and creates the GitHub release. Sequenced *after* `/release`, not instead of it. ([spec](harness/skills/ship-release.md)) |

## Telemetry

Over time, `.harness/progress.md` accumulates evidence of whether the harness is working. Run `.harness/scripts/telemetry.sh` (single-project) or `.harness/scripts/telemetry.sh --all` (multi-project, default roots: `~/Antigravity`, `~/Claude`, `~/Projects`) for a report on review rejection rate, cross-model availability, dependabot-fixer success rate, and compaction frequency. Full signal definitions and thresholds in [harness/telemetry.md](harness/telemetry.md).

## Status

Actively evolving. Releases and release notes are the source of truth — see [CHANGELOG.md](CHANGELOG.md) and the [latest release](https://github.com/alexherrero/agentic-harness/releases/latest). Re-audit the docs whenever you adopt a new model version ([principles §6](harness/principles.md)).

## Contributing

The harness is self-tested on every push to `main` and every PR by three per-OS workflows:

| Workflow | Runs on | Jobs |
|---|---|---|
| [`[T] Linux Tests`](.github/workflows/tests-linux.yml) | `ubuntu-latest` | install-smoke + adapter-parity + validate + syntax |
| [`[T] Mac Tests`](.github/workflows/tests-mac.yml) | `macos-latest` | install-smoke + validate + syntax |
| [`[T] Windows Tests`](.github/workflows/tests-windows.yml) | `windows-latest` | install-smoke (via `install.ps1`) + validate + pwsh syntax |

The three workflows run in parallel automatically. Adapter-parity is repo-invariant (runs once on Linux). Validate + syntax + cross-reference + integrity checks run on every OS, so any shell-assumption regression surfaces on the platform it broke.

**What CI verifies without running an agent or needing an API key:**
- **install-smoke** — fresh install, idempotent re-run, `--update` refreshes managed files but preserves user edits to `wiki/` and `AGENTS.md`, installer-boundary invariant (test infra never propagates to scratch), `settings.json` hook arrays have the correct shape.
- **post-install integrity** — hook-command paths in `settings.json` resolve to files that exist; every installed `.sh`/`.ps1` parses cleanly; the bash installer produces bash-shell hook commands, the pwsh installer produces pwsh-shell commands (catches fragment-picker regressions).
- **adapter-parity** — each adapter ships the canonical set of phase-commands, sub-agents, and skills with documented divergences.
- **validate** — every TOML, YAML frontmatter, and JSON across `adapters/` and `templates/` parses and has required keys.
- **check-references** — every `harness/<phases|agents|skills|pipelines>/*.md` path mentioned in an adapter file actually exists; every phase spec's "dispatch the `<name>` sub-agent / invoke the `<name>` skill" line points at a canonical spec; `settings-fragment-bash.json` and `-pwsh.json` have matching top-level event/matcher schemas.
- **syntax** — `bash -n` on every `.sh`, PowerShell AST parse on every `.ps1`, across repo root + `scripts/` + `templates/` + `adapters/`.

**Installer-boundary invariant:** the workflow files at `.github/workflows/tests-*.yml` and the helper scripts under `scripts/` live at the harness repo root, never under `templates/` or `adapters/`, so the installer never propagates them to target projects. The smoke tests assert this explicitly — if you add a test workflow or script, verify it does not appear in the scratch-install tree.

Run the same gates locally:

```bash
bash scripts/smoke-install-bash.sh      # fresh install + idempotence + --update + integrity
bash scripts/check-parity.sh            # adapter name-set invariants
bash scripts/check-syntax.sh            # bash -n on every .sh
python3 scripts/validate-adapters.py    # TOML/YAML/JSON + canonical-spec backing
python3 scripts/check-references.py     # cross-reference integrity
```

On Windows:

```pwsh
pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1   # fresh install + integrity
pwsh -NoProfile -File scripts/check-syntax.ps1          # AST-parse every .ps1
```

## License

MIT. See [LICENSE](LICENSE).
