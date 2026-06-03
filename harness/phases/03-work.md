# Phase: work

Implement exactly one task from `PLAN.md`. Stop when that task is done and its verification gates are green. Do not start the next task.

> [!NOTE]
> **State-file resolution (V4 #26 + #37).** State files live at `<vault>/projects/<slug>/_harness/<file>` post-migration. **Invoke the dispatcher CLI — don't bare-`Read .harness/<file>` or `Edit .harness/<file>`:**
>
> ```bash
> python3 scripts/harness_memory.py read-state PLAN.md       # read PLAN.md
> echo "$NEW" | python3 scripts/harness_memory.py write-state PLAN.md   # flip [ ] → [x]
> python3 scripts/harness_memory.py vault-state-path PLAN.md  # resolve path (e.g. for evidence-tracker hook)
> ```
>
> Dispatcher resolves vault path → legacy `<project>/.harness/<file>` fallback with one-warn-per-session-per-file. Writes go to vault unless local state mode is configured **on-host** (a repo-local `.project-mode` marker, or `state_mode` in `.agentm-config.json`; DC-8). Inline `.harness/<file>` refs in prose are shorthand for the dispatcher-resolved path. **The evidence-tracker hook (§5b) operates on the resolved vault path post-migration; its `**Evidence:**` matching honors the dispatcher chain transparently.**

## Purpose

The `/work` phase exists to keep implementation single-threaded and coherent. From [principles.md §3](../principles.md): parallel implementers produce mutually-inconsistent decisions; single-task sessions let the implementer hold the full context of what they're changing and why.

"One task per session" is not a suggestion. It's the load-bearing constraint that makes everything else in the harness work.

## Preconditions

1. `.harness/PLAN.md` exists with `Status: planning` or `in-progress` and at least one unchecked `[ ]` task. If not, stop — run `/plan` first.
2. Working tree is clean, or contains only intentional in-progress work from an earlier `/work` session that was interrupted.
3. `.harness/init.sh` exists. If the project's verification commands aren't obvious from it, stop and ask — better than guessing `npm test` and missing the real command.

## Inputs

- `.harness/PLAN.md` — the next unchecked task.
- `.harness/progress.md` — what happened last.
- `AGENTS.md` / `CLAUDE.md` — project-specific conventions.
- The codebase — use the `explorer` sub-agent for read-only fan-out when the task spans unfamiliar code.

## Process

### 1. Read state

- Read `PLAN.md`. Find the first unchecked task (`[ ]`). If the user specified a different task (e.g. `/work task 3`), honor that instead.
- Read `progress.md` — was a previous `/work` session interrupted? If so, confirm whether to resume or restart.
- Read `AGENTS.md` + `CLAUDE.md` for conventions (commit style, test runner, formatting).

### 1b. Auto-recall MemoryVault context (graceful-skip if not installed)

If MemoryVault is installed (`MEMORY_VAULT_PATH` env set + directory exists), load task-relevant context before confirming scope. Decisions previously made on this project's surface area inform implementation; known-issues surface "we've hit this before" patterns.

```bash
SLUG=$(python3 scripts/vault_project.py read . 2>/dev/null || true)
python3 scripts/harness_memory.py recall --phase work --project "${SLUG:-}"
```

What this loads (per `_PHASE_PROJECT_DIRS["work"]` in `harness_memory.py`):
- `personal-private/_always-load/*.md` — operator-global conventions.
- `projects/<slug>/decisions/*.md` — settled calls relevant to this codebase.
- `projects/<slug>/known-issues/*.md` — gotchas + recurring root causes (the "I've fixed this CRLF issue three times" pattern).

Budget defaults to 6k tokens (override via `HARNESS_RECALL_BUDGET_WORK` env); cap is 5 entries. Surface the recall output in the working context before §2 confirms scope. If a known-issue matches the task's domain, factor it into the confirmation prompt — operator may want to widen scope.

**Graceful-skip conditions** (silent):
- `MEMORY_VAULT_PATH` env unset or directory missing.
- `scripts/harness_memory.py available` exits 1.

See [ADR 0007](../../wiki/explanation/decisions/0007-auto-context-into-harness-phases.md) for the design rationale.

### 2. Confirm scope

Before writing any code, state the task to the user in one sentence and confirm:

> About to work on **task N: <title>**. Verification: <one-line criterion>. OK?

Skip this confirmation only if the user explicitly said "just work on the next task." The confirmation is cheap and catches plan/intent drift.

### 3. Gather context (optional)

If the task touches unfamiliar code, dispatch the `explorer` sub-agent:
> "Where is X handled? What tests exist for Y? Return a structured summary with file:line references."

Fan out if there are multiple independent questions. Do not fan out for a single question — that's slower than just reading.

### 4. Implement

- Write code that satisfies the task's "What" clause.
- Write tests that satisfy the task's "Verification" clause, in the same session. Tests-after is an anti-pattern here; verification must land with the implementation.
- Follow project conventions (formatters, naming, file layout). If the task implies a deviation, flag it and ask.
- **Do not touch `wiki/`.** Documentation updates are phase-boundary-only (see [documentation.md](../documentation.md#the-documenter-sub-agent)). Writing docs alongside code biases the implementer toward confirming the plan rather than reporting what actually shipped. Docsub runs in step 8 once gates are green.
- If mid-implementation you realize the task is bigger than planned, **stop**. Don't silently expand scope. Surface it: "Task N turned out to be bigger than planned because [reason]. Options: (a) finish the original task narrow, (b) expand the task and re-plan, (c) split into N' and N''. Your call." Then wait.

### 5. Run deterministic gates

Run these in order, short-circuit on failure:

1. **Typecheck** — project's typecheck command.
2. **Lint** — project's linter.
3. **Tests** — at least the tests for the new code; full suite if the project is small enough to run in <60s, otherwise the relevant subset.
4. **Build** — only if the task affects build output (most don't).

Commands come from `.harness/init.sh` / package scripts / Makefile — whatever the project uses. If a gate isn't configured, note it and skip, don't invent one.

### 5b. Evidence-tracking (graceful-skip if not installed)

If [`crickets`](https://github.com/alexherrero/crickets) is installed alongside the harness AND the `evidence-tracker` base hook is in place at `.claude/hooks/evidence-tracker.sh`, the harness enforces a **default-FAIL evidence contract** on every PLAN.md task: the agent must demonstrably *read* relevant spec/test/evidence files (via the `Read` tool) before a `Write`/`Edit` that flips the task's `[ ]` → `[x]` is allowed.

The hook fires on `PreToolUse` for `Read|Write|Edit`:

| Tool | Hook behavior |
|---|---|
| `Read` (file exists) | Records the path to `.harness/.evidence-reads` (per-session ephemeral state, gitignored). |
| `Read` (file missing) | No-op (prevents fictitious-path bypass — agent can't claim to have read a non-existent file). |
| `Write`/`Edit` on `.harness/PLAN.md` that flips `[ ]` → `[x]` | Resolves the task's evidence requirement; **blocks (exit 2)** if unmet, **allows (exit 0)** if met. |
| `Write`/`Edit` not flipping a checkbox | Pass-through (exit 0). |
| Any other tool | Pass-through (exit 0). |

**Task-body conventions** (per task in `.harness/PLAN.md`):

| Annotation | Behavior |
|---|---|
| *(none — default)* | **HEURISTIC** match. Any file under `tests/` or `spec/`, matching `*.spec.*` / `*.test.*` / `*_test.py` / `test_*.py` with a code extension (markdown excluded), OR any path that appears literally in the task's `**Verification:**` text. |
| `**Evidence:** <glob-or-paths>` | **Per-task override.** Comma- or whitespace-separated patterns; supports globs (`tests/foo*.py`, `src/auth/*`, etc.). Only matching reads count. |
| `**Evidence:** none — <rationale>` | **Explicit opt-out.** No reads required; flip always allowed. Use for genuinely docs-only tasks (ADRs, CHANGELOG entries, README updates). Operator acknowledges deliberately. |

**When the hook blocks you**, stderr explains exactly which paths are expected. Three recovery paths:

1. **Read a file that satisfies the requirement** (use the `Read` tool). Then retry the `[ ]` → `[x]` flip.
2. **Add an opt-out annotation** to the task body in PLAN.md if it's genuinely docs-only:
   ```markdown
   ### 7. Append CHANGELOG entry for v1.2.0
   - **What:** Add user-visible changes under the v1.2.0 header.
   - **Evidence:** none — pure documentation; no code paths to verify.
   - **Status:** [ ]
   ```
3. **Reset session state** if reads got out of sync (rare; usually means the hook was installed mid-session):
   ```bash
   python3 .claude/hooks/evidence_tracker.py --mode reset
   ```

**Graceful-skip conditions** (silent — no error, no prompt):
- `crickets` not installed (hook absent from `.claude/hooks/`).
- Python 3 not available on PATH.
- Project root has no `.harness/` directory.
- Tool input JSON is malformed (fail-open per Claude Code's PreToolUse contract).

In all skip cases, the `[ ]` → `[x]` flip proceeds without enforcement — the harness continues to work the same way it always has. Operators upgrading harness without the toolkit see zero behavior change.

This step lands per plan #9 (Evidence-tracking for `/work`). See [crickets ADR 0009 — Evidence-tracker hook](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md) for the design rationale + 3 locked design calls Q1-Q3 (lands in plan #9 task 6).

### 6. Iterate on failures

On gate failure:
- **Feed the full error output back** into the next reasoning pass. Do not summarize — the exact error is the signal.
- Cap at **5 iterations** per gate. If a gate isn't green after 5, stop and report. Loops of 20+ iterations almost always indicate a misunderstanding, not a fixable bug.
- If a test fails and the test itself is wrong — stop. Do not edit or delete the test to make it pass. Surface the test defect and ask. This is non-negotiable (see [principles.md §5](../principles.md)).

### 7. Update state

Once all gates are green:

- Edit `PLAN.md`: mark the task `[x]`. If `Status: planning`, change to `in-progress`. If this was the last task, change to `done`.
- Do NOT set `features.json` entries to `passes: true` — that's `/review`'s job, after adversarial inspection.
- Append to `progress.md`:
  ```
  <YYYY-MM-DD HH:MM> /work — completed task N: "<title>" (<filesChanged> files, <testsAdded> tests added)
  ```

### 7b. Offer-save "remember this" candidates to MemoryVault (graceful-skip if not installed)

If `harness_memory.py available` exits 0, scan the just-completed session for durable items worth promoting to MemoryVault. Candidates fall into three kinds:

| Kind | Examples |
|---|---|
| `decision` | "we picked X over Y because Z"; "ADR-shaped rationale that fits in 2 paragraphs" |
| `gotcha` | "Windows cp1252 stdout requires `sys.stdout.reconfigure(encoding='utf-8')`"; "git mv subprocess needs `-C <repo_root>` not cwd-relative path" |
| `workflow` | "the pattern we settled on for X is Y"; reusable steps for a recurring task |

For each candidate, build a short stub + offer it:

```bash
cat > /tmp/remember-<slug>.md <<EOF
# <one-line title>

<paragraph: what was decided/learned/observed, why it matters, conditions under
which it applies>

**Surfaced during:** task <N> of plan <name>
EOF

python3 scripts/harness_memory.py offer-save \
    --phase work --project "<slug>" \
    --kind <decision|gotcha|workflow> --slug "<date>-<short-slug>" \
    --content-file /tmp/remember-<slug>.md \
    --confidence <0.0-1.0> \
    --confidence-reason "<one-line rationale>"
```

**Confidence rubric** (per ADR 0007 — lands in plan #8 task 9):
- **High (≥0.85)** — direct decision/quote recorded *this session* AND matches an existing convention pattern in the vault (e.g. another `gotcha` entry confirms cross-platform Python flakiness, and the new one extends that pattern).
- **Medium (0.7)** — direct quote/decision but first-of-its-kind in the vault (no existing pattern to anchor against).
- **Low (0.5)** — inferred from session context (not explicitly stated by operator or in code) AND first-of-its-kind.

Per the self-modulating ask contract (Q4), confidence ≥ `HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` (default 0.8) saves silently with a `[auto-saved high-confidence]` stderr notice; below threshold fires the preview-and-ask prompt. Non-TTY stdin defaults to skip.

**Cap at ~3 candidates per `/work` session.** Over-firing is a failure mode — if you'd be proposing >3 saves, reconsider whether they're really durable or you're scope-creeping the offer-save into a journal-dumping exercise.

**Graceful-skip conditions** (silent):
- `harness_memory.py available` exits 1.
- `HARNESS_AUTO_SAVE_MODE=off`.
- No durable items surfaced — the cleanest case.

### 7c. Plan-done-promotion (only when this task flipped PLAN.md to `Status: done`)

If §7's PLAN.md edit just flipped `Status: in-progress → done` (this was the final unchecked `[x]`), invoke the progress.md tail-scan promotion:

```bash
python3 scripts/harness_memory.py plan-done-promotion --project-root .
```

Per the locked Q5 design call (dual-trigger middle ground): the dispatcher reads progress.md past the `.harness/.promoted-progress-cursor` byte offset, emits unpromoted entries, and advances the cursor. Idempotent — re-running within the same plan-window returns empty. The agent should then LLM-summarize the emitted tail into per-candidate offer-save calls (same machinery as §7b) — high-signal items like locked design calls + ADR-worthy decisions + cross-platform gotchas.

`/release` (task 7 of plan #8) shares the same `plan-done-promotion` trigger via the cursor file — running here means `/release`'s tail-scan will be a no-op (cursor already advanced), and vice-versa. This avoids double-prompting at the end of a plan that also ships a release.

**Graceful-skip conditions** (silent):
- This task did NOT flip PLAN.md to `Status: done`.
- `harness_memory.py available` exits 1.
- progress.md absent OR cursor already at end-of-file (idempotent no-op).

### 8. Update the wiki (post-gates)

Dispatch the `documenter` sub-agent (full spec: [`harness/agents/documenter.md`](../agents/documenter.md)) with: the task's title + What + Verification from `PLAN.md`, the diff (`git diff` scoped to the task's commits or staged changes), and the matching pending wiki entries. Docsub's job:

- Flip `Status: pending → implemented` on the matching Feature/Subsystem page(s) **only if the diff proves it** — speculative flips are a worse failure than missed ones.
- Fill `## Implementation` with real `file:line` references (GitHub URLs if a remote is set).
- Update `## Design` only if the diff shows the plan shifted during implementation. If implementation matched the plan, leave Design alone.
- Create or update pages under `wiki/how-to/` if the task introduced operational concerns (new env var, deploy step, runtime dependency, health check) — recipe shape, `## Steps`, no rationale.

If docsub returns `OPEN QUESTIONS` (e.g. "task marked `[x]` but diff doesn't touch the claimed surface"), resolve them before committing. If it returns `NO CHANGES`, that's fine — not every task touches documented surface.

**Docsub is NOT invoked during step 4 (Implement).** Only here, once gates are green. If you find yourself reaching for docsub mid-implementation, stop — the urge means the plan's intent was unclear, not that the docs need updating in-line.

### 9. Commit

One task, one commit, unless the project convention says otherwise (squash-on-merge workflows may prefer multiple). Commit message should reference the task:

```
<scope>: implement task N — <title>

<1-2 sentence why. Not what — the diff shows what.>
```

Follow project convention for trailers (issue references, `Signed-off-by`, etc.) — check recent `git log` for the style. Do **not** add a `Co-Authored-By: Claude …` trailer; the user is the sole author of intent, the agent is the tool. Only add such a trailer if the user explicitly opts in for that commit.

If the project requires signed commits or has pre-commit hooks, let them run — do not use `--no-verify`.

### 9b. Auto-orchestration: reflect the finished session (graceful-skip if not installed)

If `harness_memory.py available` exits 0, dispatch the post-`/work` reflection of the just-finished session — the auto-orchestration "push surface" (V4 #23 task 5). It mines the session transcript for durable candidates the same way the `memory-reflect-stop` hook does, but tied to **task completion** and working cross-host (including hosts without a Stop hook, e.g. Antigravity):

```bash
python3 scripts/harness_memory.py phase-dispatch post-work --project-root .
```

This is **dedup-guarded** against the `memory-reflect-stop` hook — they cooperate via the `.harness/session-id-<sid>.reflected` marker so the same transcript is never reflected twice (a second `--route` would error on a slug collision). It is config-toggleable (`enable_phase_integration` in the vault's `auto-orchestration-config.md`) and cooldown-gated (`phase_reflect_cooldown_hours`, default 1h). Non-blocking — any failure is swallowed.

**Graceful-skip conditions** (silent):
- `harness_memory.py available` exits 1 (no vault) — or the memory toolkit isn't installed.
- `enable_phase_integration = false` in the config.
- The session was already reflected (the Stop hook beat it) or the cooldown is active — the dispatch no-ops; on Claude Code the Stop hook then handles session-end reflection as before.
- **Ambiguous session** — if `.harness/` holds more than one active `session-id-*.start` marker (concurrent agents in one repo, or an active session beside a not-yet-swept crashed-session orphan), the dispatch refuses to guess which transcript is "current" and defers to the session-exact Stop hook (concurrency-safety).

### 10. Offer deferred items to the GitHub Project (optional)

If `.harness/project.json` exists and `gh` is available on PATH, consider whether this `/work` session surfaced anything **out of task scope** that the user might want on the backlog: an adjacent bug you noticed while implementing, a refactor opportunity, missing test coverage elsewhere, a stale doc. *Not* follow-ups to the current task — those belong in the next `/work` task, not a project item.

Propose one project item per distinct finding. **Batch the proposals into a single preview after the commit lands**, not interleaved during implementation — the user confirms (or declines) the whole set in one pass, or picks which to create. No count cap: if you noticed three adjacent bugs, propose three. But if you're proposing more than three in one `/work` session, reconsider — over-firing is a failure mode, and the session probably *is* scope-creeping.

Preview title + body per item. On confirmation, run for each accepted item:

```bash
gh project item-create <number> --owner <owner> \
  --title "<title>" \
  --body "<body — what you observed, where, and why it's deferred rather than in-task>"
```

reading `number` and `owner` from `.harness/project.json`.

**Graceful-skip conditions** (silent, no prompt):
- `.harness/project.json` is absent.
- `gh auth status` fails or `gh` is not on PATH.
- Nothing out-of-task-scope surfaced — the cleanest case. Over-firing is a failure mode.

Preview-and-ask is non-negotiable per [`documentation.md §GitHub Projects + Issues`](../documentation.md). If the user declines, record nothing. If accepted, reference the project item URL in the ≤5-bullet summary.

### 11. Stop

**Do not start the next task.** The next task gets its own session: either `/work` again (clean context) or `/review` first if the task warranted it.

If this task flipped a `features.json` entry's `passes` flag from `false` to `true` during `/review`, or the task finished the last feature in the plan, **suggest the `ship-release` skill** as the next step — do not auto-invoke it, the user may have more features queued. Phrase it: *"Feature `<id>` is now passing end-to-end. Consider invoking the `ship-release` skill (from crickets) to cut a tagged release."* If `crickets` isn't installed alongside, graceful-skip the suggestion — `ship-release` migrated to `crickets` in v2.0.0 (see ADR 0006).

Return to the user with a summary. Minimum (≤5 bullets — adequate for a routine task close):

- Task completed: task N, title
- Files changed: count + the most notable path
- Tests added: count, what they cover
- Gates: all green (or: "N iterations needed, here's why")
- Next: `/review` if the task is high-risk; otherwise `/work` for the next task, or `/release` if all tasks done, or `ship-release` if a feature just went green

**Enhancements when `.harness/ROADMAP.md` exists** (signals a multi-plan project — apply by default in that case):

- **Lead with roadmap context**: *"Currently building ROADMAP item #X — &lt;name&gt;. &lt;one-sentence framing&gt;."*
- **Plan-status chart** with `✅` / `⬜` symbols per task in the active plan, so the user sees where this task lands in the larger plan.
- **Link block** to `.harness/ROADMAP.md` / `.harness/PLAN.md` / `.harness/progress.md` (absolute paths; note `.harness/` is gitignored).
- **Explicit handoff phrase**: *"Say 'let's do task N' to continue"* (or *"Ending loop"* if no follow-on action).
- When relevant: commit SHA, CI status with per-OS times, key design calls or scope adjustments, manual verification scenarios, negative-test results.

These enhancements are opt-in via the ROADMAP.md signal so a harness install without a roadmap stays minimal; multi-plan projects get the navigation aids that match their scale.

## When to invoke `/review` vs. going straight to the next `/work`

Not every task needs adversarial review. Review is expensive (fresh context, adversarial framing, sometimes multiple rounds). Use it when:
- The task touches security, auth, payments, or data persistence
- The task is the last one in the plan (always review before `/release`)
- The plan's "Risks" section flagged this area
- You have a nagging feeling the change is brittle

Skip `/review` for routine changes (new tests, pure refactors, docs, scaffolding). The harness will still run gates on the next `/work` session before it does anything, so routine changes are caught by the next iteration anyway.

## Long-running `/work` — operator-control hooks (crickets)

If [`crickets`](https://github.com/alexherrero/crickets) is installed alongside the harness, three Claude-Code-only hooks land at `.claude/hooks/` and give the operator precise control over a long-running `/work` session without closing the session:

| Hook | Trigger | What it does |
|---|---|---|
| [`kill-switch`](https://github.com/alexherrero/crickets/blob/main/hooks/kill-switch/hook.md) | `PreToolUse` (every tool call) | Touch `.harness/STOP` to halt; `rm` to resume. Exits 2 + halt message on stderr; Claude Code blocks the tool call. |
| [`steer`](https://github.com/alexherrero/crickets/blob/main/hooks/steer/hook.md) | `PreToolUse` (every tool call) | Write `.harness/STEER.md` with a "do it this way instead" instruction; next tool call picks it up; file renamed to `STEER.consumed-<iso-ts>.md` for audit trail. |
| [`commit-on-stop`](https://github.com/alexherrero/crickets/blob/main/hooks/commit-on-stop/hook.md) | `Stop` event (end of each turn) | If the working tree is dirty, creates `auto-save/<iso-ts>` branch and commits the work there. Returns HEAD to the original branch with a clean tree. Recovery: `git checkout auto-save/<ts>`. |

**Why these earn their keep in long-running `/work`.** A `/work` session that hits an unexpected loop, drifts off-spec, or crashes mid-task today loses information — the only kill-switch is closing the session, the only redirect is restart, and crash recovery is hoping the working tree wasn't important. These three hooks make each precise:

- **Runaway loop**: `touch .harness/STOP` halts the next tool call without ending the session.
- **Mid-task redirect**: write `.harness/STEER.md` with the correction; the agent sees it on the next tool call without restart.
- **Crashed session / interrupted at task end**: commit-on-stop fires on the `Stop` event and saves the in-flight work to `auto-save/<ts>` — next session recovers via `git checkout`.

**Ordering invariant.** `kill-switch` and `steer` both fire on `PreToolUse`. Alphabetical install order means `kill-switch` runs first — if `.harness/STOP` is present, the tool call is blocked **before** `steer` reads `STEER.md`. Halt always takes precedence over a steer.

**Graceful-skip.** Install `crickets` to enable; otherwise `/work` runs without the hooks. The phase contract doesn't require them — they're an operator-precision layer on top of the existing workflow. See [crickets's how-to](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Base-Hooks.md) for installation + worked scenarios.

## Failure modes to avoid

- **Starting the next task "while you're in there."** The single most common way `/work` breaks coherence. Stop after one.
- **Editing tests to make them pass.** Banned. If a test is wrong, say so and stop.
- **Skipping failed gates** ("I'll fix this next session"). Gates must be green before the task is marked `[x]`.
- **Silently expanding scope.** If the task is bigger than planned, surface it — don't quietly do more than the plan says.
- **Summarizing errors instead of passing them through** on gate failure. The full error is information; a summary is a guess at what matters.
- **Committing without running gates.** Gates first, commit second.
- **Forgetting to update PLAN.md and progress.md.** The next session is blind without them.
- **Implementing across multiple tasks in one commit.** Messy to review, messy to revert.

## Output to the user (at end of `/work`)

Example:

> Completed task 2: "Core line-counting with file inputs" in `~/lc/`
> - 3 files changed (`src/cli.js`, `src/count.js`, `test/count.test.js`)
> - 4 tests added (fixture-based line counts, error on unreadable file)
> - Gates: typecheck N/A, lint clean, all tests pass, no build step
> - Committed as `feat: implement file-mode line counting`
> - Next: `/work` for task 3 (stdin support), or `/review` if you want a critic pass first

Tight. No essay.
