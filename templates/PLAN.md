# Plan: <short title>

**Status:** planning
**Created:** <YYYY-MM-DD>
**Brief:** <1-3 sentence restatement of what we're building or changing>

## Goal

<What success looks like in 2-4 sentences. User-facing language, not implementation-flavored.>

## Constraints

- <Non-obvious constraint — performance, compatibility, deadline, regulatory, etc.>

## Out of scope

- <Explicit non-goal. At least one.>

## Tasks

### 1. <Task title>
- **What:** <1-2 sentences describing the concrete change>
- **Verification:** <executable check — a test to add, a command to run, a user flow to exercise>
- **Evidence:** <OPTIONAL — only when crickets's evidence-tracker hook is installed.
    Default behavior is heuristic match against `tests/` + `spec/` + `*.spec.*` /
    `*.test.*` / `*_test.py` + paths named in `**Verification:**`. Use this field to
    override (e.g. `**Evidence:** custom/path/*.md`) or to opt-out for docs-only
    tasks (`**Evidence:** none — pure ADR write`).>
- **Status:** [ ]

### 2. <Task title>
- **What:**
- **Verification:**
- **Status:** [ ]

## Risks / open questions

- <What could go wrong, what we'll do if it does>
- <Any decision we may need to defer until mid-work>

## Verification strategy

<Which deterministic gates apply — typecheck, lint, tests, build, e2e. Any project-specific extras.>
