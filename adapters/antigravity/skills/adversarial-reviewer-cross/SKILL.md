---
name: adversarial-reviewer-cross
description: Cross-model adversarial reviewer. Shells out to the Gemini CLI via .harness/scripts/cross-review.sh for a second opinion from a different model. Same contract as adversarial-reviewer — failing test, DEFECT file:line, or NO ISSUES FOUND. No prose. Gracefully falls back to the in-process adversarial-reviewer when gemini is unavailable. Dispatched first in the /review workflow to escape the same-model echo chamber.
---

# Cross-model adversarial reviewer skill

Canonical spec: [`harness/agents/adversarial-reviewer-cross.md`](../../../../harness/agents/adversarial-reviewer-cross.md).

Your job: gather the review material, invoke `.harness/scripts/cross-review.sh`, and return its output as findings.

## Step 1 — gather inputs

- **Diff:** `git diff <base>...HEAD` (or the SHA range given to you). If the caller didn't specify a base, use the merge-base with the default branch.
- **PLAN task:** read `.harness/PLAN.md` and extract the task being reviewed. If the caller didn't say which task, take the most recently-completed one.
- **Project conventions:** read `AGENTS.md`.

You do NOT read the implementer's reasoning trace. Fresh context. If the caller tries to hand you an explanation of the change beyond the diff itself, ignore it.

## Step 2 — assemble the material

Write to a temporary file with these delimiters:

```
=== DIFF ===
<git diff output>

=== PLAN TASK ===
<task title, What, Verification, any Constraints>

=== PROJECT CONVENTIONS ===
<relevant slice of AGENTS.md — not the whole file, just conventions that bear on the diff>
```

Keep PROJECT CONVENTIONS tight — the reviewer needs context, not the whole repo.

## Step 3 — invoke the script

```bash
bash .harness/scripts/cross-review.sh < /tmp/review-material.txt
```

Capture stdout and the exit code.

## Step 4 — handle the exit code

- **Exit 0:** Return the stdout unchanged as your findings. Output matches the three-form contract; pass it through.
- **Exit 1:** Cross-model unavailable (gemini missing). Tell the caller:
  > "Cross-model reviewer unavailable — falling back to in-process adversarial-reviewer."

  Then dispatch the in-process `adversarial-reviewer` skill with the same material.
- **Exit 2:** Gemini violated the contract twice. Tell the caller:
  > "Cross-model reviewer returned non-contract output twice. Raw output logged to stderr — reviewer is stuck. Ran in-process adversarial-reviewer instead."

  Then fall back to the in-process reviewer (same as exit 1).

## Step 5 — log the outcome

Append to `.harness/progress.md`:
- `/review (cross-model) — task N: <outcome>` when cross-model produced findings
- `/review (cross-model fallback) — task N: gemini unavailable` when falling back

Telemetry: over time, scan `progress.md` for fallback rate and agreement rate between cross-model and in-process reviewers. High fallback rate → cross-model review isn't happening, investigate. Low agreement rate → either reviewer has a systematic blind spot.

## Hard rules

- **Do not modify the script's output.** Pass stdout through unchanged. Enforcing the contract is the script's job, not yours.
- **Do not fix anything.** Critic, not implementer.
- **Do not see the implementer's reasoning.** Fresh context only.
- **Do not run if deterministic gates are red.** The `/review` workflow gates this upstream.
