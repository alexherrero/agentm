# Verification

How the harness verifies that work is actually done. Deterministic first, LLM second.

## Deterministic gates (mandatory in `/review` and `/release`)

Run in order. Short-circuit on first failure — feed the full error output back to the implementer, don't summarize.

1. **Typecheck** — `npm run typecheck` / `tsc --noEmit` / `go vet` / equivalent for the project's language.
2. **Lint** — project's configured linter. If none configured, skip (but note it in `/setup`).
3. **Unit tests** — `npm test` / `go test ./...` / equivalent. All pass required.
4. **Integration tests** — if they exist. Timeout-sensitive; run in CI even if skipped locally.
5. **Build** — the production build must succeed. Dev-server-only success is insufficient.

These commands are defined per project in `.harness/init.sh` so every phase uses the same ones.

## LLM review (in `/review` only, after deterministic gates pass)

The adversarial reviewer is invoked once deterministic gates are green. See [agents/adversarial-reviewer.md](agents/adversarial-reviewer.md) for the spec.

The reviewer looks for:
- Spec adherence — does the change actually satisfy the active plan's task criteria (`PLAN.md` or `PLAN-<name>.md`)?
- API design — public interfaces, naming, error types.
- Dead code or accidental duplication.
- Security concerns that don't have a lint rule.
- Edge cases not covered by tests.

The reviewer **must** produce either:
- A failing test case (preferred), OR
- A specific `file.ts:line` reference to a concrete defect, OR
- An explicit "no issues found" — logged so rejection rate can be tracked.

Prose-only critiques ("consider adding error handling") are rejected.

## What's not verification

- "The agent says it implemented the feature" — unverified, ignore.
- "The code looks right" — LLM assessment without a deterministic check, ignore.
- "A previous session said this was done" — check `.harness/features.json`'s `passes` field, re-run verification if unsure.

## Feature pass criteria (in `features.json`)

A feature may only be marked `passes: true` after:
1. All deterministic gates pass with the feature exercised.
2. Where relevant, end-to-end test (real user flow, not code inspection) confirms the feature works.
3. The adversarial reviewer either cleared it or its findings were addressed in a follow-up commit.
