# Hooks

Optional Claude Code hooks that strengthen the harness. Installed by `install.sh --hooks`.

All hook scripts live in `.harness/hooks/` (per-project, editable). Hook *registration* lives in `.claude/settings.json`.

## PostToolUse — verification after every Write/Edit

After every Write or Edit, runs `.harness/verify.sh <file>`. Lets the harness's verification gates fire incrementally, not just at `/review` time.

- Script: `.harness/verify.sh` (per-project — uncomment your stack's typecheck/lint)
- Matcher: `Write|Edit`
- Timeout: 10s

## PreCompact — write a marker before context is wiped

Compaction (manual `/compact` or automatic) wipes most of the conversation. The hook appends a timestamped "compaction event" entry to `.harness/progress.md` so the post-compaction session has an explicit anchor in the durable state.

- Script: `.harness/hooks/precompact.sh`
- Matcher: `manual|auto`
- Pure side-effect — never blocks compaction
- stdin payload: `{ trigger: "manual"|"auto", custom_instructions: "..." }`

## SessionStart (matcher: `compact`) — re-anchor Claude on the state files

Fires once when a session resumes after compaction. Outputs a reminder that points Claude at the active plan file (`.harness/PLAN.md`, or a named `PLAN-<name>.md`) and `.harness/progress.md`. Claude Code injects the stdout into the post-compaction context.

- Script: `.harness/hooks/session-start-compact.sh`
- Matcher: `compact` (does not fire on normal session start)

## Why both `PreCompact` and `SessionStart`

`PreCompact` writes the breadcrumb. `SessionStart` reads the room afterwards and tells Claude where the breadcrumbs are. Either alone is half the loop:

- `PreCompact` only: marker exists but Claude has no reason to read it.
- `SessionStart` only: Claude is told to look at progress.md, but progress.md has no marker for *this* compaction.

Together: the marker is written, then Claude is reliably pointed at it.

## What hooks must never do

- Modify code or tests (use a slash command if you want that)
- Block compaction silently (exit 2 blocks; only do this if the user explicitly opts in)
- Run anything that takes more than a few seconds — hooks have short timeouts and slow hooks ruin the editing loop
- Log to stdout from `PostToolUse` (Claude reads it as user input — only `SessionStart` and `UserPromptSubmit` should emit context)
