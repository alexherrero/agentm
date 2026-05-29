---
name: doctor
description: Verify the agentm install in this project is correctly wired up. Trigger when the user says "check my harness install", "is the harness working", "run doctor", or invokes /doctor. Default mode is structural only (no tokens, <5s) — checks that expected phase commands, sub-agents, skills, state files, and hooks are present and parseable. The --live flag adds real sub-agent dispatches and skill dry-runs to prove end-to-end wiring (~30–90s, moderate token cost). Never installs or mutates state; reports gaps and points at install.sh.
---

You are running the `doctor` skill. Full canonical spec: `harness/skills/doctor.md` in the agentm repo. The summary below is the operational version.

## Input handling

- **No argument** → default mode: structural discovery only.
- **`--live`** → default checks plus live probes (one dispatch per sub-agent, one dry-run per skill).
- **`--live --verbose`** → same as `--live` but print raw probe output on each row.

## Adapter detection + install scope

This is the Claude Code adapter. Resolve install scope before checking primitives:

- **Project scope** — `<project>/.claude/commands/` populated. Expected sets must live under `<project>/.claude/`.
- **User scope (default since V4 #30 v4.3.0)** — `<project>/.claude/commands/` empty/absent but `~/.claude/commands/` populated. Run the full structural battery against `~/.claude/`.
- **Mixed** — both populated. Validate each scope's set independently.
- **Neither** — abort: `doctor: no Claude Code install detected at project or user scope — run install.sh /path/to/project`.

Use whichever scope holds the primitives as `$CC_ROOT` for the rest of the structural checks. Report the resolved scope on the output (`scope: user|project|mixed`).

## Default-mode checks

Expected name sets:

| Surface | Required | Optional (graceful-skip if absent) |
|---|---|---|
| `$CC_ROOT/commands/*.md` | `bugfix, plan, release, review, setup, work` | `recent-wiki-changes` (V4 #30 plan 2 / v4.4.0+) |
| `$CC_ROOT/agents/*.md` | `adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer` | `memory-idea-researcher` (harness); `adapt-evaluator, diataxis-evaluator, evaluator` (crickets) |
| `$CC_ROOT/skills/*/` | `doctor, migrate-to-diataxis, wiki-author` (wiki-author since V4 #30 plan 2 / v4.4.0) | `design, diataxis-author, memory, ship-release` (harness compound); `dependabot-fixer, pii-scrubber` (crickets) |

"Required" must be present and parse cleanly — missing or broken is FAIL. "Optional" is reported as `[OK] N present` or `[SKIP] not installed` — never FAIL. Extras outside both lists are reported as a soft `note:` row, not a failure.

For each expected file:
1. Exists at the right path.
2. Frontmatter YAML parses cleanly (no trailing-tab or quote issues).
3. `name:` field matches dirname — **only** for sub-agents and skills (which carry an explicit `name:`). Claude Code phase commands intentionally have no `name:` field; their name is implicit from the filename. Don't flag them for a missing `name:`.

Then:
4. **State files (V4 #26-aware)**: Resolve via this two-step ladder:
   - **Vault-resident (post-v4.1.0 default)** — shell out to `python3 <agentm-repo>/scripts/harness_memory.py vault-state-path PLAN.md` (and same for `progress.md`). The subcommand exits 0 + prints the path when resolved, exits 1 + empty output when no vault path is configured. If both paths resolve and exist on disk, report `state files [OK] vault-resident — <vault-path>` and move on.
   - **Legacy `.harness/`** — if the resolver returns nothing or the vault is unavailable, check `<project>/.harness/PLAN.md` + `<project>/.harness/progress.md`. Report `state files [OK] legacy .harness/` if both present.
   - FAIL only if neither path yields both files. An empty `.harness/` alongside a healthy vault resolution is the EXPECTED post-V4 #26 shape — not a fail.
   - Note: `scripts/telemetry.sh` is no longer a vault-resident state file (v4.6.2+). It's a user-scope helper — see check 4b below.
4b. **Helper scripts (user-scope; v4.6.2+).** Check `<prefix>/scripts/telemetry.sh` exists + is executable. Report `[OK] telemetry.sh installed` if present. Report `[WARN] telemetry.sh not installed — re-run install.sh` if absent (graceful, never FAIL). The script roots across multiple projects (`--all` scans `~/Antigravity`, `~/Claude`, `~/Projects`), so it lives at user scope, not per-project.
5. `AGENTS.md` + `CLAUDE.md` exist at repo root.
6. **Hook wiring (V4 #39 — a real check, not "absent block is fine").** Hooks install at user scope (`~/.claude/hooks/<name>/`) under `--scope user`; the installer MUST merge each hook's `settings-fragment-bash.json` into `<prefix>/settings.json` (V4 #39 task 1). Resolve the prefix (`$AGENTM_INSTALL_PREFIX` → `~/.claude`) and apply this truth table against `<prefix>/hooks/` + `<prefix>/settings.json` (apply the same logic to a populated legacy project-scope `<project>/.claude/`):

   | Disk state | Report |
   |---|---|
   | `hooks/` empty + no `hooks` block in settings.json | `[OK] no hooks installed (clean)` |
   | `hooks/` populated + `hooks` block present + **every** registered `command` path resolves to an existing file + **every** installed hook dir has a registered fragment | `[OK] N hooks wired (<comma-list>)` |
   | `hooks/` populated + **no `hooks` block** | **`[FAIL] N hooks installed on disk but not wired in settings.json — install.sh fragment merge did not run. Re-run install.sh.`** ← the V4 #39 bug |
   | `hooks/` populated + `hooks` block + some `command` paths point at missing files | `[FAIL] X of N registered hook commands point at missing scripts: <list>` |
   | `hooks/` populated + `hooks` block + some installed hook dirs not registered | `[WARN] <list> installed but not registered — partial merge` |
   | `<prefix>/.agentm-config.json` missing while user-scope primitives present | `[WARN] partial install — install-state file missing` |
   | `.agentm-config.json` lacks a `fragments` field (or it's empty) while hooks ARE installed | `[WARN] fragments tracking absent — install-state-sync won't propagate source-clone edits` |

   Also confirm bash-installed commands are bash-shell (not pwsh). The pre-V4 #39 behavior — treating an absent `hooks` block as "opt-in, OK" — was a **false-clean**: it masked the exact regression where hook dirs were installed but never registered.

Report a pass/fail table. Exit here unless `--live` was passed.

## `--live` probes

Run in order. Stop at first foundational failure — structural breakage makes later probes meaningless.

### 1. `explorer` dispatch

Dispatch `explorer` with:
> *Return the absolute path of `README.md` and `AGENTS.md` at the repo root. One sentence each, no commentary.*

Pass: returns both absolute paths within 60s.

(Earlier versions of this probe asked for `.harness/PLAN.md` — that path may not exist post-V4 #26 when state is vault-resident. `AGENTS.md` is a stable repo-root marker that doesn't move.)

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

### 7. Synthetic SessionStart probe (V4 #39; best-effort per DC-3)

Send a synthetic SessionStart event JSON (`{"session_id":"doctor-probe","cwd":"<agentm clone>"}`) on stdin to each registered SessionStart hook script and capture stdout. Confirm at least `harness-context-session-start` returns a non-empty context block — agentm is a harness cwd, so it should emit the `[agentm] Project state…` header + at least one resolved path. **Best-effort:** skip gracefully (report **skip**, not fail) if a hook script can't be exercised standalone. The load-bearing gate is the structural hook-wiring check (#6 above); this probe is confirmation that a wired SessionStart hook actually fires.

**Additionally** assert `memory-recall-session-start` emits **non-empty stdout** when the configured vault has any `<vault>/personal-private/_always-load/*.md` entries:

- Count always-load entries: `find "$vault_path/personal-private/_always-load" -maxdepth 1 -name '*.md' | wc -l`.
- If count > 0 AND the probe's stdout is empty → **`[FAIL] memory-recall-session-start exits 0 but emits nothing despite N always-load entries in vault — script-path or vault-path resolution silently failing`**. This is the silent-broken shape (V4.7 / agentm-hooks regression): pre-fix, the hook hardcoded a project-scope relative path to `recall.py` and assumed `MEMORY_VAULT_PATH` was injected by Claude Code into the hook env — neither held on user-scope installs.
- If count == 0 → empty stdout is correctly OK.

Pass: `harness-context-session-start` emits a 2-path block matching the expected shape AND, when vault has always-load entries, `memory-recall-session-start` emits a `# MemoryVault — always-load entries` header followed by entry bodies.

## Output contract

```
doctor: claude-code — <PASS|FAIL>

  scope:              user        (or: project | mixed)
  state mode:         vault-resident   (or: legacy .harness/)

  structural:
    phase-commands    [OK]  6/6 required + 1 optional (recent-wiki-changes) present
    sub-agents        [OK]  4/4 required, 5 optional present
    skills            [OK]  3/3 required, 6 optional present
    state files       [OK]  vault-resident — <vault>/projects/<slug>/_harness/
    host wiring       [OK]  AGENTS.md + CLAUDE.md
    hooks             [OK]  10 hooks wired (memory-recall-session-start, install-state-sync, …)

  live probes (--live):
    explorer          [OK]   2.1s
    adversarial       [OK]   3.4s
    ship-release      [OK]   1.8s  — proposed v0.9.0, no tag written
    migrate-diataxis  [OK]   0.9s  — no-op (marker present)
    dependabot-fixer  [OK]   1.2s
    verify.sh         [SKIP] ruff not installed
    sessionstart      [OK]   0.3s  — harness-context-session-start injected vault paths

summary: 11 OK, 0 FAIL, 1 SKIP

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
