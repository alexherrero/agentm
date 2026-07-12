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
| Claude Code | `<project>/.claude/agents/` + `<project>/.claude/skills/` | `~/.claude/agents/` + `~/.claude/skills/` |
| Antigravity | `<project>/.agents/rules/` + `<project>/.agents/skills/` | `~/.agents/rules/` + `~/.agents/skills/` |
| Gemini | `<project>/.gemini/settings.json` + `<project>/.agents/skills/` | `~/.gemini/settings.json` + `~/.agents/skills/` |

**Post-slim marker shift (V5 dev-loop slim).** The dev-loop primitives — phase commands (`.claude/commands/`, `.gemini/commands/`, `.agents/workflows/`) and the review sub-agents — are no longer agentm's install marker: they moved to the crickets developer-workflows / code-review plugins and may or may not be present. agentm's *durable* surface is the memory-engine sub-agents (`.claude/agents/`), the shared skills (`.claude/skills/` + `.agents/skills/`), and the host wiring (Antigravity `.agents/rules/`, Gemini `.gemini/settings.json`). Detect on those. A crickets-installed `.agents/workflows/` or `.gemini/commands/` may coexist but does not, on its own, indicate an agentm install.

**Install scope detection (V4 #30 v4.3.0+).** Since v4.3.0, `install.sh --scope user` is the default. When the project-scope path is empty or absent but the user-scope path has the expected primitives, doctor reports `scope: user` and runs the full structural battery against `~/.claude/` (or the host equivalent). When both scopes have primitives, doctor reports `scope: mixed` and validates each scope's set independently. When neither has primitives, abort with `doctor: no harness adapter found at project or user scope — run install.sh first`.

Multiple adapters may be present in the same project (the installer supports that). Run the full battery against each one found and report per-adapter.

## Default-mode checks (structural)

For each detected adapter, verify the expected name set is present and each file parses. The expected sets come from the same source as `scripts/check-parity.sh`:

- **Phase commands** (harness-vendored): `recent-wiki-changes` (V4 #30 plan 2, v4.4.0+) is the only phase/utility command agentm still vendors — graceful-skip if absent on a pre-v4.4.0 install. The six phase-gated dev-loop commands (`bugfix, plan, release, review, setup, work`) moved to the crickets **developer-workflows** plugin in the V5 dev-loop slim — report `[OK] present` if crickets is paired, `[SKIP] not installed` if absent, **never FAIL**. A bare agentm install has no dev-loop commands and that is the expected, healthy shape (DC-2: agentm is unaware of the dev loop, no pointer, no requirement).
- **Sub-agents** (required, harness-shipped): `adapt-evaluator, memory-idea-researcher` — the memory-engine sub-agents agentm keeps. Crickets-provided (graceful-skip if crickets is not paired, **never FAIL**): `adversarial-reviewer, adversarial-reviewer-cross, explorer` (code-review / developer-workflows — moved out of agentm in the V5 dev-loop slim) and `diataxis-evaluator, documenter, evaluator` (wiki-maintenance — `documenter` retired from agentm in the seven-section convergence, canonical in crickets' `wiki-maintenance` plugin).
- **Skills** (required, harness-shipped): `doctor, wiki-author` (wiki-author landed in V4 #30 plan 2 / v4.4.0). Optional harness-shipped compound skills: `design, memory` — graceful-skip if absent (they may be deferred via `install.sh --no-compound-skills` or similar). Optional crickets-shipped skills: `dependabot-fixer, diataxis-author, pii-scrubber, ship-release` — graceful-skip if crickets is not paired (`diataxis-author` retired from agentm in the seven-section convergence and absorbs the old four-mode `migrate-to-diataxis` migration via `/diataxis migrate`; canonical in crickets' `wiki-maintenance` plugin; `ship-release` retired its agentm-local copy 2026-07-01 and is now fully owned by crickets' `releasing-conventions` skill of the same name, covering both discipline and mechanics).

For each expected item:
1. The file exists at the adapter-specific path (project scope or user scope, whichever the install resolved to).
2. The frontmatter YAML (markdown) or top-level TOML parses cleanly.
3. **For surfaces that carry an explicit `name:` field**, the field matches the filename/dirname. Surfaces that carry `name:`: Claude Code sub-agents and skills, Antigravity skills (including sub-agents-as-skills), Gemini sub-agents. Surfaces *without* `name:` (name is implicit from filename): Claude Code phase commands, Antigravity workflows, Gemini TOML commands. Do **not** flag missing `name:` on those.

Then:
4. **State files (V4 #26-aware)**: Resolve via this two-step ladder:
   - **Vault-resident (post-v4.1.0 default)** — invoke `python3 <agentm>/scripts/harness_memory.py vault-state-path PLAN.md` (and same for `progress.md`). The CLI exits 0 + prints the resolved path when the vault is reachable AND the project is registered; exits 1 + empty stdout otherwise (graceful-skip signal). PASS if the resolver returns paths that exist on disk.
   - **Legacy `.harness/<file>` fallback** — if `vault-state-path` exits non-zero (vault unreachable from this shell, or project not registered), check `<project>/.harness/PLAN.md` + `<project>/.harness/progress.md`. PASS if both exist.
   - **Named plans (V5-10)** — enumerate `PLAN*.md` in the resolved `_harness/` dir (the parent of the `vault-state-path PLAN.md` result), skipping GDrive `*(conflicted copy*` files. Report the full set, e.g. `state files [OK] vault-resident — 2 named plans: PLAN-foo.md, PLAN-bar.md (+ progress-<name>.md each)`. A repo carrying only named plans and **no** unnamed `PLAN.md` is healthy — see the FAIL rule below.
   - **Dangling active-plan marker** — if `<project>/.harness/active-plan` exists, read its first line as `<name>` and confirm `PLAN-<name>.md` resolves in that `_harness/` dir. Present-but-unresolvable (names an absent or empty `PLAN-<name>.md`) is **`[WARN] .harness/active-plan -> <name> is dangling — PLAN-<name>.md not found`** — never FAIL. This mirrors the session-start hook's non-fatal surfacing; `resolve_active_plan` is the loud-error enforcer at bind time, `doctor` is only the reporter.
   - Report which mode resolved (e.g. `state files [OK] vault-resident — <vault>/projects/<slug>/_harness/` or `state files [OK] legacy .harness/`).
   - FAIL only if neither resolution path produces **any** plan file — no unnamed `PLAN.md`/`progress.md` pair AND no `PLAN-<name>.md`. A `.harness/` empty of state files alongside a healthy vault resolution is the EXPECTED V4 #26+ shape — not a fail; a named-only repo (named plans present, unnamed singleton absent) is likewise not a fail.
   - `telemetry.sh` (pre-v4.6.2 also checked here) moved to user scope in v4.6.2 — see "Helper scripts" below. Per-project vault copies of `scripts/telemetry.sh` are no longer expected.

4b. **Helper scripts (user-scope; v4.6.2+).** Check `<prefix>/scripts/telemetry.sh` exists + is executable. PASS if present. WARN (graceful-skip, never FAIL) if absent — older installs predate the move. Reason for the move: `telemetry.sh` roots across multiple projects (its `--all` flag scans `~/Antigravity`, `~/Claude`, `~/Projects`), so a single user-scope copy is the right shape; per-project vault copies create N stale duplicates when the script changes.
4c. **Worktree slug-safety (V5-10).** Run `python3 <agentm>/scripts/vault_project.py check-worktree-slug <project>` — the same shared resolver the `check-worktree-slug` gate calls, so the probe and the gate can't drift. It compares the full-chain vault slug against the Tier-3 origin basename — the slug a fresh `git worktree` resolves to, since a worktree shares the parent's remotes but not the gitignored `.harness/` where a Tier-1/2 override would live. Map the exit code:
   - `0` → `[OK] worktree slug-safe — slug '<slug>' == origin basename`.
   - `1` → `[WARN] slug '<resolved>' != origin basename '<origin>' — a worker in a git worktree would resolve to '<origin>' and write under the wrong projects/<slug>/. Align the slug with the origin basename, or adopt the crickets worktree-spawn fallback that reproduces a divergent vault_project into the worktree.` **Never FAIL here:** exactly like the dangling active-plan marker above, `doctor` is the reporter and the `check-worktree-slug` gate (in `scripts/check-all.sh` / CI) is the loud, build-blocking enforcer.
   - `3` → `[OK] no origin remote — worktree slug-safety not applicable` (a worktree would resolve to no slug and graceful-skip; not a foot-gun).
   Most informative run from inside a worktree, but meaningful in any checkout — a divergence seen in the main checkout is the latent warning that a worktree of this project *would* misroute its writes.
4d. **Storage-backend preview (V5-1).** Run `python3 <agentm>/scripts/backend_selection.py --doctor` — the same `backend_selection` resolver the memory engine selects through, reusing the identical `_install_plugin_message` the task-3 fail-loud guard raises, so the preview and the live refusal can't drift. It resolves the selected backend (explicit `storage.backend` → existing `vault_path` → fresh-install `device-local` default), checks whether that protocol's plugin is registered (`registry.get` resolves), and — for `device-local` — whether the root is writable, all **without constructing a backend** (construction would `mkdir` the root). Map the single status line + exit code:
   - status `ok`, exit `0` → `[OK]` — selected backend is registered and ready (`vault` seeded from the resolved `vault_path`, a writable `device-local` root, or a registered third-party protocol).
   - status `warn`, exit `0` → `[WARN]` — `device-local` selected but its root is not writable. The engine will still try, but the write will fail loudly; surfacing it here is preventive, never build-blocking.
   - status `fail`, exit `1` → `[FAIL]` — the selected backend has no registered plugin (prints the verbatim install-the-plugin message the task-3 guard raises), or `vault` is selected with no resolvable `vault_path`, or the config file exists but is unparseable / names a non-string `storage.backend`. **This is the one structural check that legitimately FAILs** (unlike the worktree-slug reporter above): it is the fail-loud preview shown *before* the engine itself refuses — doctor previewing exactly what selection will do, not second-guessing a separate enforcer.

4e. **Memory MCP server (V5-9).** Run `python3 <agentm>/scripts/memory_mcp_doctor.py` to check the health of the standalone memory MCP daemon. **Graceful-skip if `memory_mcp_doctor.py` is absent** — report `[SKIP] memory-server not installed (pre-V5-9)`, never FAIL. The MCP server is not a required component of a bare agentm install.

   Default mode runs `liveness` + `token_env`; `--live` adds `origin_guard` + `index_root_safe` (`--all` flag):

   - **`liveness`** — GET `/health` at `AGENTM_MCP_URL` (default `http://127.0.0.1:7821`); expects `{"status":"ok"}`. FAIL if daemon unreachable or body unexpected; remedy names the launchctl bootstrap command.
   - **`token_env`** — `AGENTM_MCP_TOKEN` is set and non-empty. FAIL if unset; remedy names the `launchctl setenv` command.
   - **`origin_guard`** (`--live`) — POST to `/mcp` with `Origin: http://evil.example.com`; expects 403. SKIP if daemon is down. FAIL if daemon returns non-403 (Origin-validation not wired).
   - **`index_root_safe`** (`--live`) — vault_mutex lock root is not inside a synced/cloud-backed path (`/CloudStorage/`, `/Dropbox/`, etc.). FAIL if lock root is inside a synced tree; remedy names the `XDG_CACHE_HOME` fix.

   Map results:
   - `passed=True` → `[OK]  memory-server <name>: <msg>`
   - `passed=False` → `[FAIL] memory-server <name>: <msg — includes named remedy>`
   - `passed=None` → `[SKIP] memory-server <name>: <reason>`

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

7. **Vec-index freshness (R1.4 / agentmExperience#0).** When a vault is reachable, run `python3 <agentm>/harness/skills/memory/scripts/vault_lint.py --vault <vault> --check-freshness --format json`. Parse the printed `ratio`. `[OK]` if `ratio >= 0.80` or the vault has zero indexable entries yet. `[WARN] vec-index freshness N% (<up_to_date>/<total> entries) — the drain may be dead; run vec_index.py full-sync --rebuild then drain` otherwise. Never `[FAIL]` — this is a visibility signal (the drain being behind is recoverable, not a broken install), so it surfaces on a routine `/doctor` run within a day of going stale rather than requiring a dedicated live probe.

8. **Machinery integrity (Consolidation follow-ups batch, machinery-integrity lane).** The prior checks all ask "is agentm's own harness distribution installed correctly"; this one asks the operator's own question — "how do we know all these structures are working consistently right now?" — over this repo's *own* dev-loop machinery (its Stop hook, its scheduled runner jobs, its cross-repo bridges), the exact class of thing that sat merged-but-never-installed for weeks in two separate confirmed incidents (the session-cost-capture hook in this repo; crickets' cross-review Gemini-fallback degrading silently). Run `python3 <agentm>/scripts/machinery_doctor.py` (only meaningful from inside an agentm dev checkout — skip with `[SKIP] not an agentm dev checkout` otherwise) and map each printed row by its status:
   - `[OK]` → report as-is; include the `(last fired …)` timestamp when the row carries one — a structure installed but never observed to fire is a different, less-reassuring state than one with a recent timestamp, even though both currently read `[OK]`/`[WARN]` correctly.
   - `[WARN]` → report as-is, never escalate to FAIL. Covers: a manually-installed dev-safety git hook (`commit-msg`, `prepare-commit-msg`) not present on this machine, and a shipped runner-job template not yet copied into `.harness/jobs/` — both are legitimate, expected states on a fresh clone or an opt-in-only job, not regressions.
   - `[FAIL]` → report as-is. This is the row shape that would have caught both confirmed incidents: the Stop-hook wiring check FAILs if `.claude/settings.json`'s `Stop` block doesn't reference `session-cost-capture.sh`, or if it does but the script file is missing on disk — the exact "merged but never installed"/"installed then silently broken" shapes.
   - `[UNVERIFIED]` (with an `owner:`) → report as-is; never silently drop the row. Surfaces the cross-repo pieces (the crickets coordination-check suite, the cross-review degradation marker) this repo alone can't independently confirm when no crickets sibling checkout is reachable.

   `--live` adds nothing further here — the script's own checks are already cheap, structural, read-only reads (no sub-agent dispatch), so there's no separate live tier to gate behind the flag.

## `--live` probes

Run in order. First failure stops the battery for that adapter (the rest will only produce noise if the foundation is broken).

### Probe 1: `explorer` sub-agent dispatch

**Graceful-skip if not installed.** `explorer` moved to the crickets developer-workflows plugin in the V5 dev-loop slim. If the sub-agent isn't present in any host's agent paths, report **skip** with reason: *"explorer sub-agent not found — install the crickets developer-workflows plugin to enable this probe."* Never FAIL on its absence — a bare agentm install legitimately has no `explorer` (DC-2).

If installed: dispatch the `explorer` sub-agent with a trivial prompt that only requires filesystem access:

> *Return the absolute path of `README.md` and `AGENTS.md` at the repo root. One sentence each, no commentary.*

**Pass criteria:** agent returns within 60s; output contains both absolute paths; no tool-permission errors.
**Fail signals:** the sub-agent isn't visible to the host (adapter registration broken), permission denied on read (sandbox mis-wired), agent hallucinates paths without reading.

The pre-V4 #26 form of this probe used `.harness/PLAN.md` as the second target. That path may not exist when state is vault-resident; `AGENTS.md` is a repo-root anchor that doesn't move.

### Probe 2: `adversarial-reviewer` sub-agent dispatch

**Graceful-skip if not installed.** `adversarial-reviewer` moved to the crickets code-review plugin in the V5 dev-loop slim. If the sub-agent isn't present, report **skip** with reason: *"adversarial-reviewer sub-agent not found — install the crickets code-review plugin to enable this probe."* Never FAIL on its absence (DC-2).

If installed: dispatch with a deliberately-buggy snippet inline in the prompt:

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

### Probe 4: diataxis migration preview (crickets-provided — graceful-skip)

agentm no longer ships a migration skill — the four-mode `migrate-to-diataxis` retired to crickets' `wiki-maintenance` (`/diataxis migrate`) in the V5 docs slim. If that skill is absent (a bare agentm install), **skip** this probe — report `[SKIP] not installed`, never FAIL. If crickets is paired, optionally invoke `/diataxis migrate --preview` against the current `wiki/`; with the `wiki/.diataxis` marker present it should no-op cleanly with "already migrated".

**Pass criteria:** the probe is skipped on a bare agentm; or, when crickets is paired, the migration preview detects the marker and proposes no moves.
**Fail signals:** doctor hard-FAILs because the migration skill is absent (it must graceful-skip), or a paired crickets migration proposes re-classifications of already-placed files.

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

### Probe 10: Capability resolver sanity (V5-8)

Always runs (not gated on crickets). Exercises the V5-8 capability resolver against the current host state to verify it can be imported and returns a valid result without raising.

```python
python3 "<agentm>/scripts/capability_resolver.py" "nonexistent-capability-doctor-probe"
```

**Expected behavior:** exits 1 (unavailable) — the probe uses a capability name that no real plugin will ever declare, so "unavailable" is the correct answer regardless of what plugins are installed. Exit 2 is a FAIL (usage error in the shim). An unhandled exception printed to stderr is a FAIL.

**Pass criteria:** exit 0 or exit 1 (both mean the resolver ran); no Python traceback on stderr; `capability_resolver.py` importable.
**Fail signals:** `capability_resolver.py` not found (V5-8 not shipped or wrong install path); Python syntax/import error; exit 2 (usage regression in `_main`).
**Skip:** never — this probe is always cheap (it reads no files on an empty host).

If an installed plugin happens to declare `"nonexistent-capability-doctor-probe"` as a capability, the probe will exit 0 — that is still a pass (the resolver worked correctly). The probe name is deliberately unlikely; the check is liveness, not absence.

## Output contract

```
doctor: <adapter> — <PASS|FAIL>

  scope:              user        (or: project | mixed)
  state mode:         vault-resident   (or: legacy .harness/)

  structural:
    phase-commands    [OK]  recent-wiki-changes present; 6 dev-loop commands crickets-provided ([SKIP] if unpaired)
    sub-agents        [OK]  2/2 required (adapt-evaluator, memory-idea-researcher); review agents crickets-provided
    skills            [OK]  2/2 required (doctor, wiki-author);
                            2 optional harness-shipped + crickets present
    state files       [OK]  vault-resident — <vault>/projects/<slug>/_harness/
    worktree-slug     [OK]  slug 'agentm' == origin basename — worktree-safe
    storage           [OK]  selected backend 'vault' (existing vault_path) — registered; seeded from <vault>
                            # FAIL example (V5-1): "storage [FAIL] storage backend 'foo' is
                            # configured (storage.backend) but no installed plugin registers it.
                            # Install the plugin that provides the 'foo' backend, or set
                            # storage.backend to an installed backend (currently registered: …)."
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  6 hooks wired (memory-recall-session-start, harness-context-session-start, …)
                            # FAIL example (V4 #39): "6 hooks installed on disk but not
                            # wired in settings.json — install.sh fragment merge did not run"
    machinery         [OK]  3 OK, 12 WARN, 0 FAIL, 0 UNVERIFIED (python3 scripts/machinery_doctor.py)
                            # FAIL example (the confirmed incident this check exists for):
                            # "stop-hook:session-cost-capture.sh [FAIL] settings.json has no
                            # Stop hook referencing session-cost-capture.sh — re-run the wiring step"

  live probes (--live):
    explorer          [SKIP] crickets developer-workflows not installed — probe needs the explorer sub-agent
    adversarial       [SKIP] crickets code-review not installed — probe needs the adversarial-reviewer sub-agent
    ship-release      [OK]   1.8s  — proposed v0.9.0, no tag written
    migrate-diataxis  [OK]   0.9s  — no-op (marker present)
    dependabot-fixer  [OK]   1.2s  — no matching PRs
    hooks             [SKIP] ruff not installed — cannot exercise *.py case

summary: 7 OK, 0 FAIL, 4 SKIP
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
