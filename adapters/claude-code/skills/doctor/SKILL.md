---
name: doctor
description: Verify the agentm install in this project is correctly wired up. Trigger when the user says "check my harness install", "is the harness working", "run doctor", or invokes /doctor. Default mode is structural only (no tokens, <5s) — checks that expected phase commands, sub-agents, skills, state files, and hooks are present and parseable. The --live flag adds real sub-agent dispatches and skill dry-runs to prove end-to-end wiring (~30–90s, moderate token cost). Never installs or mutates state; reports gaps and points at install.sh.
---

You are running the `doctor` skill. Full canonical spec: `harness/skills/doctor.md` in the agentm repo. The summary below is the operational version.

## Input handling

- **No argument** → default mode: structural discovery only.
- **`--live`** → default checks plus live probes (one dispatch per sub-agent, one dry-run per skill).
- **`--live --verbose`** → same as `--live` but print raw probe output on each row.

## Host + adapter detection

`doctor` ships to all three agentm host adapters from one source — **detect which host you're in from disk first**, then run that host's structural battery. The expected NAME sets (phase commands, sub-agents, skills) are identical across hosts; only the paths and hook-applicability differ.

| Host | Detect (disk marker) | Phase commands | Sub-agents | Skills | Hooks |
|---|---|---|---|---|---|
| **Claude Code** | `.claude/agents/` or `.claude/skills/` present | `$ROOT/commands/*.md` (only `recent-wiki-changes`; dev-loop commands crickets-provided) | `$ROOT/agents/*.md` (memory agents; review agents crickets-provided) | `$ROOT/skills/*/` | `$ROOT/hooks/` + `settings.json` |
| **Antigravity** | `.agents/rules/` present, no `.claude/` | crickets-provided (`.agents/workflows/*.md` if paired) | `.agents/skills/*/` (memory agents; review agents crickets-provided) | `.agents/skills/*/` | none — skip |
| **Gemini CLI** | `.gemini/settings.json` present, no `.claude/` | crickets-provided (`.gemini/commands/*.toml` if paired) | crickets-provided (`.gemini/agents/*.md` if paired) | `.agents/skills/*/` (shared delivery) | none — skip |

- **Claude Code only — resolve install scope** before checking: project (`<project>/.claude/` populated), **user** (`~/.claude/` populated — default since v4.3.0), or mixed; use whichever holds the primitives as `$ROOT` and report it (`scope: user|project|mixed`).
- **Antigravity** reads `.agents/` (the 2.0 default; `.agent/` singular is the pre-V4 #22 legacy path, migrated on `--update`). Its sub-agents *and* skills both live under `.agents/skills/`. Always-on rules: `.agents/rules/harness.md` + `.agents/rules/agentmemory-context.md`.
- **No hook surface on Antigravity / Gemini** — skip the hook-wiring check (#6) and the hook/SessionStart probes (#6–#7); report them `[SKIP] no hook surface on <host>`, never FAIL.
- **None detected** — abort: `doctor: no agentm install detected (.claude/, .agents/, or .gemini/) — run install.sh /path/to/project`.

Report the detected `host:` on the output, and use the detected host's primitive paths as `$ROOT` for the checks below.

## Default-mode checks

Expected name sets:

| Surface | Required (harness-shipped) | Crickets-provided / optional (graceful-skip if absent — never FAIL) |
|---|---|---|
| `$ROOT/commands/*.md` | — (`recent-wiki-changes` optional, V4 #30 plan 2 / v4.4.0+) | `bugfix, plan, release, review, setup, work` (crickets developer-workflows — moved out of agentm in the V5 dev-loop slim) |
| `$ROOT/agents/*.md` | `adapt-evaluator, memory-idea-researcher` (memory engine) | `adversarial-reviewer, adversarial-reviewer-cross, explorer` (crickets code-review / developer-workflows); `diataxis-evaluator, documenter, evaluator` (crickets wiki-maintenance) |
| `$ROOT/skills/*/` | `doctor, wiki-author` (wiki-author since V4 #30 plan 2 / v4.4.0) | `design, memory` (harness compound); `dependabot-fixer, diataxis-author, pii-scrubber, ship-release` (crickets — `diataxis-author` absorbs the retired four-mode `migrate-to-diataxis` migration; `ship-release` fully crickets-owned since 2026-07-01) |

The table above is written with Claude Code's surfaces (`commands` / `agents` / `skills`); on **Antigravity** map them per the detection table — sub-agents *and* skills both live under `.agents/skills/*/` (the same Required name sets apply); on **Gemini** the required skills come from the shared `.agents/skills/*/` delivery. The dev-loop surfaces (phase commands + review agents) are crickets-provided on every host — `[OK] present` if crickets is paired, `[SKIP] not installed` if absent, never FAIL.

"Required" must be present and parse cleanly — missing or broken is FAIL. "Optional" is reported as `[OK] N present` or `[SKIP] not installed` — never FAIL. Extras outside both lists are reported as a soft `note:` row, not a failure.

For each expected file:
1. Exists at the right path.
2. Frontmatter YAML parses cleanly (no trailing-tab or quote issues).
3. `name:` field matches dirname — **only** for sub-agents and skills (which carry an explicit `name:`). Phase commands (Claude Code `.md` / Antigravity workflows) intentionally have no `name:` field; their name is implicit from the filename. Don't flag them for a missing `name:`.

Then:
4. **State files (V4 #26-aware)**: Resolve via this two-step ladder:
   - **Vault-resident (post-v4.1.0 default)** — shell out to `python3 <agentm-repo>/scripts/harness_memory.py vault-state-path PLAN.md` (and same for `progress.md`). The subcommand exits 0 + prints the path when resolved, exits 1 + empty output when no vault path is configured. If both paths resolve and exist on disk, report `state files [OK] vault-resident — <vault-path>` and move on.
   - **Legacy `.harness/`** — if the resolver returns nothing or the vault is unavailable, check `<project>/.harness/PLAN.md` + `<project>/.harness/progress.md`. Report `state files [OK] legacy .harness/` if both present.
   - FAIL only if neither path yields both files. An empty `.harness/` alongside a healthy vault resolution is the EXPECTED post-V4 #26 shape — not a fail.
   - Note: `scripts/telemetry.sh` is no longer a vault-resident state file (v4.6.2+). It's a user-scope helper — see check 4b below.
4b. **Helper scripts (user-scope; v4.6.2+).** Check `<prefix>/scripts/telemetry.sh` exists + is executable. Report `[OK] telemetry.sh installed` if present. Report `[WARN] telemetry.sh not installed — re-run install.sh` if absent (graceful, never FAIL). The script roots across multiple projects (`--all` scans `~/Antigravity`, `~/Claude`, `~/Projects`), so it lives at user scope, not per-project.
4c. **Storage-backend preview (V5-1).** Shell out to `python3 <agentm-repo>/scripts/backend_selection.py --doctor` — the same resolver the memory engine selects through, reusing the identical install-the-plugin message the fail-loud guard raises. It resolves the selected backend (explicit `storage.backend` → existing `vault_path` → fresh `device-local`), confirms that protocol's plugin is registered, and (for `device-local`) that its root is writable — read-only, never constructing a backend. Print its single status line and map: `[OK]` (exit 0) ready; `[WARN]` (exit 0) `device-local` root not writable — preventive, never FAIL; `[FAIL]` (exit 1) unregistered plugin (prints the verbatim install-the-plugin message), `vault` with no `vault_path`, or a corrupt / non-string config. **The one structural check that legitimately FAILs** — it's the fail-loud preview shown *before* the engine refuses.
4d. **Memory MCP server (V5-9).** Shell out to `python3 <agentm-repo>/scripts/memory_mcp_doctor.py`. **Graceful-skip if absent** — `[SKIP] memory-server not installed (pre-V5-9)`, never FAIL. Default mode checks `liveness` + `token_env`; `--live` adds `origin_guard` + `index_root_safe`. Map each result:
   - `passed=True` → `[OK]  memory-server <name>: <msg>`
   - `passed=False` → `[FAIL] memory-server <name>: <msg — includes named remedy>`
   - `passed=None` → `[SKIP] memory-server <name>: <reason>`

   Four checks:
   - `liveness` — GET `/health`; expects `{"status":"ok"}`. FAIL remedy: `launchctl bootstrap gui/$UID com.agentm.memory-mcp-server`.
   - `token_env` — `AGENTM_MCP_TOKEN` set and non-empty. FAIL remedy: `launchctl setenv AGENTM_MCP_TOKEN <token>`.
   - `origin_guard` (`--live`) — spoofed Origin expects 403; SKIP if daemon down.
   - `index_root_safe` (`--live`) — lock root outside synced/cloud path.
5. `AGENTS.md` + `CLAUDE.md` exist at repo root.
6. **Hook wiring (V4 #39 — a real check, not "absent block is fine"). _Claude Code only — on Antigravity/Gemini report `[SKIP] no hook surface` and move on._** Hooks install at user scope (`~/.claude/hooks/<name>/`) under `--scope user`; the installer MUST merge each hook's `settings-fragment-bash.json` into `<prefix>/settings.json` (V4 #39 task 1). Resolve the prefix (`$AGENTM_INSTALL_PREFIX` → `~/.claude`) and apply this truth table against `<prefix>/hooks/` + `<prefix>/settings.json` (apply the same logic to a populated legacy project-scope `<project>/.claude/`):

   | Disk state | Report |
   |---|---|
   | `hooks/` empty + no `hooks` block in settings.json | `[OK] no hooks installed (clean)` |
   | `hooks/` populated + `hooks` block present + **every** registered `command` path resolves to an existing file + **every** installed hook dir has a registered fragment | `[OK] N hooks wired (<comma-list>)` |
   | `hooks/` populated + **no `hooks` block** | **`[FAIL] N hooks installed on disk but not wired in settings.json — install.sh fragment merge did not run. Re-run install.sh.`** ← the V4 #39 bug |
   | `hooks/` populated + `hooks` block + some `command` paths point at missing files | `[FAIL] X of N registered hook commands point at missing scripts: <list>` |
   | `hooks/` populated + `hooks` block + some installed hook dirs not registered | `[WARN] <list> installed but not registered — partial merge` |
   | `<prefix>/.agentm-config.json` missing while user-scope primitives present | `[WARN] partial install — install-state file missing` |

   Also confirm bash-installed commands are bash-shell (not pwsh). The pre-V4 #39 behavior — treating an absent `hooks` block as "opt-in, OK" — was a **false-clean**: it masked the exact regression where hook dirs were installed but never registered.

7. **Machinery integrity (Consolidation follow-ups batch).** Shell out to `python3 <agentm-repo>/scripts/machinery_doctor.py` (only meaningful inside an agentm dev checkout — `[SKIP] not an agentm dev checkout` otherwise). This asks a different question than the checks above: not "is agentm's harness distribution installed," but "is THIS repo's own dev-loop machinery — its Stop hook, its scheduled runner jobs, its cross-repo bridges — actually wired on this machine right now," the exact class of gap that let the session-cost-capture hook and crickets' cross-review Gemini fallback sit merged-but-never-installed / silently-degraded for weeks. Report the script's own summary line (`N OK, N WARN, N FAIL, N UNVERIFIED`) plus every non-OK row verbatim:
   - `[WARN]` rows (an optional git hook not installed, a shipped job template not yet registered) never escalate to FAIL — expected on a fresh clone.
   - `[FAIL]` rows are real: e.g. `.claude/settings.json`'s `Stop` block no longer references `session-cost-capture.sh`, or does but the script file went missing.
   - `[UNVERIFIED]` rows (with an `owner:`) mean this repo alone can't confirm a cross-repo piece (no crickets sibling reachable) — surface plainly, never drop.

Report a pass/fail table. Exit here unless `--live` was passed.

## `--live` probes

Run in order. Stop at first foundational failure — structural breakage makes later probes meaningless.

### 1. `explorer` dispatch

**Graceful-skip if not installed.** `explorer` moved to the crickets developer-workflows plugin in the V5 dev-loop slim. If the sub-agent isn't present, report **skip** (*"explorer not found — install crickets developer-workflows to enable this probe"*), never FAIL — a bare agentm install legitimately has no `explorer` (DC-2).

If installed, dispatch `explorer` with:
> *Return the absolute path of `README.md` and `AGENTS.md` at the repo root. One sentence each, no commentary.*

Pass: returns both absolute paths within 60s.

(Earlier versions of this probe asked for `.harness/PLAN.md` — that path may not exist post-V4 #26 when state is vault-resident. `AGENTS.md` is a stable repo-root marker that doesn't move.)

### 2. `adversarial-reviewer` dispatch

**Graceful-skip if not installed.** `adversarial-reviewer` moved to the crickets code-review plugin in the V5 dev-loop slim. If the sub-agent isn't present, report **skip** (*"adversarial-reviewer not found — install crickets code-review to enable this probe"*), never FAIL (DC-2).

If installed, dispatch with this inline prompt:

> *Review this function for bugs. Report the single most important defect as a failing test or a specific file:line. Prose-only critiques are rejected.*
>
> ````python
> def divide(a, b):
>     return a / b  # no zero-check
> ````

Pass: returns an executable artifact (failing test or file:line pointer), not prose.

### 3. `ship-release --dry-run`

**Graceful-skip if not installed.** `ship-release` is crickets-owned. If the skill isn't present in any host's skill paths, report **skip** (*"ship-release skill not found — install crickets to enable this probe"*), never FAIL.

If installed, invoke the `ship-release` skill with `--dry-run`.

Pass: prints a proposed `vX.Y.Z` and notes; `git tag --list` unchanged; `git status` still clean.

### 4. diataxis migration preview (crickets-provided — graceful-skip)

agentm no longer ships a migration skill (the four-mode `migrate-to-diataxis` retired to crickets' `wiki-maintenance` in the V5 docs slim). **Skip** if absent — `[SKIP] not installed`, never FAIL. If crickets is paired, `/diataxis migrate --preview` against `wiki/` with the `.diataxis` marker present should no-op.

Pass: skipped on a bare agentm; or the paired crickets preview detects the marker and proposes no moves.

### 5. `dependabot-fixer` no-match path

Invoke with no matching Dependabot PRs open.

Pass: one-line "no matching PRs", exit 0.

### 6. Hook synthetic trigger (optional) — Claude Code only

Skip on Antigravity/Gemini (no hook surface). Only if `.claude/settings.json` has hooks. Exercise the project's `verify.sh` against an empty scratch file under `$TMPDIR` with a matching extension. Report **skip** (not fail) if project tooling (`ruff`, `npx`, etc.) is missing.

Pass: verify command exits 0 on the empty file.

### 7. Synthetic SessionStart probe (V4 #39; best-effort per DC-3) — Claude Code only

Skip on Antigravity/Gemini (no hook surface). Send a synthetic SessionStart event JSON (`{"session_id":"doctor-probe","cwd":"<agentm clone>"}`) on stdin to each registered SessionStart hook script and capture stdout. Confirm at least `harness-context-session-start` returns a non-empty context block — agentm is a harness cwd, so it should emit the `[agentm] Project state…` header + at least one resolved path. **Best-effort:** skip gracefully (report **skip**, not fail) if a hook script can't be exercised standalone. The load-bearing gate is the structural hook-wiring check (#6 above); this probe is confirmation that a wired SessionStart hook actually fires.

**Additionally** assert `memory-recall-session-start` emits **non-empty stdout** when the configured vault has any `<vault>/personal/_always-load/*.md` entries:

- Count always-load entries: `find "$vault_path/personal/_always-load" -maxdepth 1 -name '*.md' | wc -l`.
- If count > 0 AND the probe's stdout is empty → **`[FAIL] memory-recall-session-start exits 0 but emits nothing despite N always-load entries in vault — script-path or vault-path resolution silently failing`**. This is the silent-broken shape (V4.7 / agentm-hooks regression): pre-fix, the hook hardcoded a project-scope relative path to `recall.py` and assumed `MEMORY_VAULT_PATH` was injected by Claude Code into the hook env — neither held on user-scope installs.
- If count == 0 → empty stdout is correctly OK.

Pass: `harness-context-session-start` emits a 2-path block matching the expected shape AND, when vault has always-load entries, `memory-recall-session-start` emits a `# MemoryVault — always-load entries` header followed by entry bodies.

## Output contract

```
doctor: claude-code — <PASS|FAIL>     (host: claude-code | antigravity | gemini)

  host:               claude-code
  scope:              user        (Claude Code only; or: project | mixed)
  state mode:         vault-resident   (or: legacy .harness/)

  structural:
    phase-commands    [OK]  recent-wiki-changes present; 6 dev-loop commands crickets-provided ([SKIP] if unpaired)
    sub-agents        [OK]  2/2 required (adapt-evaluator, memory-idea-researcher); review agents crickets-provided
    skills            [OK]  3/3 required, optional present
    state files       [OK]  vault-resident — <vault>/projects/<slug>/_harness/
    storage           [OK]  selected backend 'vault' (existing vault_path) — registered; seeded from <vault>
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  6 hooks wired (memory-recall-session-start, harness-context-session-start, …)
    machinery         [OK]  3 OK, 12 WARN, 0 FAIL, 0 UNVERIFIED (python3 scripts/machinery_doctor.py)

  live probes (--live):
    explorer          [SKIP] crickets developer-workflows not installed
    adversarial       [SKIP] crickets code-review not installed
    ship-release      [OK]   1.8s  — proposed v0.9.0, no tag written
    migrate-diataxis  [OK]   0.9s  — no-op (marker present)
    dependabot-fixer  [OK]   1.2s
    verify.sh         [SKIP] ruff not installed
    sessionstart      [OK]   0.3s  — harness-context-session-start injected vault paths

summary: 7 OK, 0 FAIL, 4 SKIP

# Example of the V4 #39 regression the new hook-wiring check now catches:
#     hooks             [FAIL] 10 hooks installed on disk but not wired in
#                              settings.json — install.sh fragment merge did not
#                              run. Re-run install.sh.
```

On any `FAIL`, print the specific reason under the failing row, exit non-zero, do not auto-repair.

## Guardrails

- Never run `--live` probes without an explicit `--live` flag or spoken user consent.
- Never write to the repo working tree. Scratch files go under `$TMPDIR`.
- Never invoke a skill without its dry-run / preview flag in probe mode.
- Stop at the first foundational failure; don't compound noise.
