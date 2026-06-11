# Skill: doctor

**Purpose:** verify an installed agentm is actually wired up correctly in *this* host — that the expected sub-agents, skills, slash commands, and hooks are discoverable and runnable. Companion to `templates/scripts/telemetry.sh`: telemetry answers "is the harness being used well over time?"; `doctor` answers "is it installed correctly right now?".

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

| Adapter | Project-scope marker | User-scope marker |
|---|---|---|
| Claude Code | `<project>/.claude/commands/` + `<project>/.claude/agents/` | `~/.claude/commands/` + `~/.claude/agents/` |
| Antigravity | `<project>/.agent/workflows/` + `<project>/.agents/skills/` | `~/.agents/skills/` |
| Gemini | `<project>/.gemini/commands/` + `<project>/.gemini/agents/` | `~/.gemini/commands/` + `~/.gemini/agents/` |

**Install scope detection (V4 #30 v4.3.0+).** Since v4.3.0, `install.sh --scope user` is the default. When the project-scope path is empty or absent but the user-scope path has the expected primitives, doctor reports `scope: user` and runs the full structural battery against `~/.claude/` (or the host equivalent). When both scopes have primitives, doctor reports `scope: mixed` and validates each scope's set independently. When neither has primitives, abort with `doctor: no harness adapter found at project or user scope — run install.sh first`.

Multiple adapters may be present in the same project (the installer supports that). Run the full battery against each one found and report per-adapter.

## Default-mode checks (structural)

For each detected adapter, verify the expected name set is present and each file parses. The expected sets come from the same source as `scripts/check-parity.sh`:

- **Phase commands** (required): `bugfix, plan, release, review, setup, work`. Plus `recent-wiki-changes` (V4 #30 plan 2, v4.4.0+) — graceful-skip if absent on a pre-v4.4.0 install.
- **Sub-agents** (required): `adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer`. Optional extras shipped by harness in V3+: `memory-idea-researcher, adapt-evaluator`. Optional extras shipped by crickets: `diataxis-evaluator, evaluator` (graceful-skip if crickets not paired).
- **Skills** (required, harness-shipped): `doctor, migrate-to-diataxis, wiki-author` (wiki-author landed in V4 #30 plan 2 / v4.4.0). Optional harness-shipped compound skills: `design, memory, ship-release` — graceful-skip if absent (they may be deferred via `install.sh --no-compound-skills` or similar). Optional crickets-shipped skills: `dependabot-fixer, diataxis-author, pii-scrubber` — graceful-skip if crickets is not paired (`diataxis-author` retired from agentm in the seven-section convergence; canonical in crickets' `wiki-maintenance` plugin).

For each expected item:
1. The file exists at the adapter-specific path (project scope or user scope, whichever the install resolved to).
2. The frontmatter YAML (markdown) or top-level TOML parses cleanly.
3. **For surfaces that carry an explicit `name:` field**, the field matches the filename/dirname. Surfaces that carry `name:`: Claude Code sub-agents and skills, Antigravity skills (including sub-agents-as-skills), Gemini sub-agents. Surfaces *without* `name:` (name is implicit from filename): Claude Code phase commands, Antigravity workflows, Gemini TOML commands. Do **not** flag missing `name:` on those.

Then:
4. **State files (V4 #26-aware)**: Resolve via this two-step ladder:
   - **Vault-resident (post-v4.1.0 default)** — invoke `python3 <agentm>/scripts/harness_memory.py vault-state-path PLAN.md` (and same for `progress.md`). The CLI exits 0 + prints the resolved path when the vault is reachable AND the project is registered; exits 1 + empty stdout otherwise (graceful-skip signal). PASS if the resolver returns paths that exist on disk.
   - **Legacy `.harness/<file>` fallback** — if `vault-state-path` exits non-zero (vault unreachable from this shell, or project not registered), check `<project>/.harness/PLAN.md` + `<project>/.harness/progress.md`. PASS if both exist.
   - Report which mode resolved (e.g. `state files [OK] vault-resident — <vault>/projects/<slug>/_harness/` or `state files [OK] legacy .harness/`).
   - FAIL only if neither resolution path produces both files. A `.harness/` empty of state files alongside a healthy vault resolution is the EXPECTED V4 #26+ shape — not a fail.
   - `telemetry.sh` (pre-v4.6.2 also checked here) moved to user scope in v4.6.2 — see "Helper scripts" below. Per-project vault copies of `scripts/telemetry.sh` are no longer expected.

4b. **Helper scripts (user-scope; v4.6.2+).** Check `<prefix>/scripts/telemetry.sh` exists + is executable. PASS if present. WARN (graceful-skip, never FAIL) if absent — older installs predate the move. Reason for the move: `telemetry.sh` roots across multiple projects (its `--all` flag scans `~/Antigravity`, `~/Claude`, `~/Projects`), so a single user-scope copy is the right shape; per-project vault copies create N stale duplicates when the script changes.
5. **Host wiring file**: `AGENTS.md` exists at repo root. Adapter-specific overlay file exists (`CLAUDE.md` for Claude Code, `.gemini/settings.json` for Gemini pointing at `AGENTS.md`).
6. **Hook wiring** (Claude Code; V4 #39 — a real check, not "absent block is fine"). Hooks install at user scope (`<prefix>/hooks/<name>/`, prefix = `$AGENTM_INSTALL_PREFIX` → `~/.claude`) under `--scope user`; the installer MUST merge each hook's `settings-fragment-bash.json` into `<prefix>/settings.json` (V4 #39 task 1). Apply this truth table to `<prefix>/hooks/` + `<prefix>/settings.json` (and a populated legacy project-scope `<project>/.claude/` likewise):
   - `hooks/` empty + no `hooks` block → `[OK] no hooks installed (clean)`.
   - `hooks/` populated + `hooks` block + **every** registered `command` resolves to an existing file + **every** installed hook dir has a registered fragment → `[OK] N hooks wired (<list>)`.
   - `hooks/` populated + **no `hooks` block** → **`[FAIL] N hooks installed on disk but not wired in settings.json — install.sh fragment merge did not run. Re-run install.sh.`** (the V4 #39 regression).
   - `hooks/` populated + `hooks` block + some `command` paths missing → `[FAIL] X of N registered hook commands point at missing scripts: <list>`.
   - `hooks/` populated + `hooks` block + some installed hook dirs unregistered → `[WARN] <list> installed but not registered — partial merge`.
   - `.agentm-config.json` missing while primitives present → `[WARN] partial install — install-state file missing`.
   - Shell prefix must match the installer variant (bash → bash command; pwsh → `pwsh -File`). The pre-V4 #39 "absent block is opt-in, OK" rule was a **false-clean** that masked exactly the hook-dirs-installed-but-unregistered regression.
   - `--live` adds a **synthetic SessionStart probe** (best-effort, DC-3): feed `{"session_id":"doctor-probe","cwd":"<agentm clone>"}` to each registered SessionStart hook on stdin; confirm `harness-context-session-start` emits a non-empty `[agentm] Project state…` block; skip gracefully if a hook can't run standalone.
   - **The probe also asserts `memory-recall-session-start` emits non-empty stdout WHEN the configured vault has any `<vault>/personal-private/_always-load/*.md` entries.** Exit 0 with empty stdout in that condition is **`[FAIL] memory-recall-session-start exits 0 but emits nothing despite N always-load entries in vault — script-path or vault-path resolution silently failing`** — the silent-broken shape (V4.7 / agentm-hooks regression). If the vault has zero always-load entries, empty stdout is correctly OK.

## `--live` probes

Run in order. First failure stops the battery for that adapter (the rest will only produce noise if the foundation is broken).

### Probe 1: `explorer` sub-agent dispatch

Dispatch the `explorer` sub-agent with a trivial prompt that only requires filesystem access:

> *Return the absolute path of `README.md` and `AGENTS.md` at the repo root. One sentence each, no commentary.*

**Pass criteria:** agent returns within 60s; output contains both absolute paths; no tool-permission errors.
**Fail signals:** the sub-agent isn't visible to the host (adapter registration broken), permission denied on read (sandbox mis-wired), agent hallucinates paths without reading.

The pre-V4 #26 form of this probe used `.harness/PLAN.md` as the second target. That path may not exist when state is vault-resident; `AGENTS.md` is a repo-root anchor that doesn't move.

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

**Graceful-skip if not installed.** `ship-release` migrated to `crickets` in v2.0.0. If the skill isn't present in any host's skill paths (check `.claude/skills/ship-release/`, `.agent/skills/ship-release/`, `.agents/skills/ship-release/`), report **skip** with reason: *"ship-release skill not found — install crickets to enable this probe."*

If installed: invoke `ship-release --dry-run`. This should compute a proposed version and notes **without** tagging or pushing.

**Pass criteria:** skill prints a proposed `vX.Y.Z`, classifies the commit range, and exits cleanly without side effects. `git tag --list` is unchanged. `git status` still clean.
**Fail signals:** skill actually creates a tag (guardrail broken), skill crashes on the preconditions check, `gh auth status` failure surfaces without being caught.

### Probe 4: `migrate-to-diataxis` preview on already-migrated tree

Invoke `migrate-to-diataxis` in preview mode against the current `wiki/`. If `wiki/.diataxis` marker is present, the skill should no-op cleanly with "already migrated".

**Pass criteria:** skill detects the marker, prints the no-op message, exits without proposing moves.
**Fail signals:** skill proposes re-classifications of already-placed files (classification logic broken), or crashes reading the marker.

### Probe 5: `dependabot-fixer` "nothing matched" path

**Graceful-skip if not installed.** `dependabot-fixer` migrated to `crickets` in v2.0.0. Report **skip** with reason if the skill isn't found.

If installed: invoke `dependabot-fixer` with no matching Dependabot PRs open. The skill should exit cleanly with "no matching PRs found", not crash or try to fix a non-existent PR.

**Pass criteria:** one-line "nothing to fix" output, exit 0.
**Fail signals:** the skill tries to check out a PR branch, or fails on `gh pr list` parsing.

### Probe 6: hook synthetic trigger (Claude Code + `--hooks`, optional)

Only runs if `.claude/settings.json` has a hooks block. Write a trivial no-op file change to a scratch file under `/tmp/` with the project's configured verify command applied manually (not through a real Write tool invocation, to avoid actually modifying the repo). Verify the command runs and exits 0 on an empty file.

**Pass criteria:** the configured verify command exits 0 on an empty file of the matching extension.
**Fail signals:** verify.sh not executable, wrong interpreter, missing dependency.

This probe is best-effort: if `verify.sh` requires project-specific tooling (a specific `npx`, `ruff`, etc.) and that tooling isn't installed, report **skip** with the reason, not **fail**.

### Probe 7: Antigravity CLI (`agy`) discoverability (v1.2.0+)

Only runs when the Antigravity adapter is detected. Verifies that `agy` is on PATH + the Antigravity 2.0 plugin directory is healthy:

```bash
agy --version                                # should print 1.0.2+
test -d ~/.gemini/config/plugins             # plugins root exists
```

**Pass criteria:** both checks succeed.
**Fail signals:** `agy: command not found` (host install incomplete) OR `~/.gemini/config/plugins` missing (agy install didn't complete onboarding).
**Skip:** `agy --version` returns < v1.0.2 (older agy may lack plugin discovery — see crickets ADR 0011 for the surface this probe targets).

### Probe 8: Antigravity skill discovery at `.agents/skills/` (v1.2.0+)

Only runs when the Antigravity adapter is detected. Verifies that a known crickets-installed skill landed at the correct plural path:

```bash
test -f .agents/skills/evaluator/SKILL.md    # or any known evaluator/sub-agent
```

**Pass criteria:** the file exists at `.agents/skills/<name>/SKILL.md`.
**Fail signals:** file at `.agent/skills/<name>/` (singular — pre-v1.2.0 crickets install; suggest `bash install.sh --update <project>` to migrate) OR not present at all (skill not installed).
**Skip:** no crickets-managed sub-agents declared `supported_hosts: [antigravity]` (nothing expected to be found).

### Probe 9: Plugin discovery (Antigravity 2.0 + agy CLI; v1.2.0+)

Only runs when the Antigravity adapter is detected AND at least one `kind: plugin` customization is locally installable. Verifies that crickets's user-global plugin install path works end-to-end:

```bash
bash /path/to/crickets/scripts/install-plugin.sh --list
test -f ~/.gemini/config/plugins/example-plugin/plugin.json    # if example-plugin installed
```

**Pass criteria:** `install-plugin.sh --list` runs without error AND (if any plugin is installed) the `plugin.json` file at `~/.gemini/config/plugins/<plugin-name>/plugin.json` is valid JSON.
**Fail signals:** `install-plugin.sh` not found in crickets toolkit; `plugin.json` malformed.
**Skip:** no `kind: plugin` customizations installed (nothing to verify).

## Output contract

```
doctor: <adapter> — <PASS|FAIL>

  scope:              user        (or: project | mixed)
  state mode:         vault-resident   (or: legacy .harness/)

  structural:
    phase-commands    [OK]  6/6 required + 1 optional (recent-wiki-changes) present
    sub-agents        [OK]  4/4 required present, frontmatter valid
    skills            [OK]  3/3 required (doctor, migrate-to-diataxis, wiki-author);
                            4 optional harness-shipped + 2 crickets present
    state files       [OK]  vault-resident — <vault>/projects/<slug>/_harness/
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  7 hooks wired (memory-recall-session-start, harness-context-session-start, …)
                            # FAIL example (V4 #39): "7 hooks installed on disk but not
                            # wired in settings.json — install.sh fragment merge did not run"

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
| Antigravity 2.0 / agy CLI | Prompt: *"Run the doctor skill"* (optionally `--live`). Skill reads from `.agents/skills/doctor/SKILL.md` per the Antigravity 2.0 v1.2.0+ convention. |

## Guardrails

- **Never run `--live` without the user's explicit `--live` flag or spoken consent.** Live probes cost tokens.
- **Never write to the repo working tree.** Scratch files go under `/tmp/` or `$TMPDIR`.
- **Never invoke a skill without its dry-run / preview flag** in probe mode. The probes assert no-op semantics; a probe that tags a release would be a bug.
- **Stop at the first foundational failure.** If structural checks fail, skip `--live` probes — they'll just compound the noise.
