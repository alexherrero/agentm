---
name: done
kind: opinion
question: "is it finished?"
serves: [development-lifecycle]
implements: crickets/development-lifecycle
composes: []
---
Finished means the deterministic check battery is green and the plan/task
state on disk says so — `bash scripts/check-all.sh` (or the project's
equivalent) passing, the task marked `[x]`, `progress.md` updated. An LLM
judgment that "this looks done" is not this standard; the battery is.
