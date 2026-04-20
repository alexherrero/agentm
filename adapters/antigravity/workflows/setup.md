---
description: First-time project scaffold — populate init.sh, features.json, AGENTS.md with real commands, seed wiki/. Run once per project.
---

# Setup workflow

Run the **setup** phase of agentic-harness. Full spec: [`harness/phases/01-setup.md`](../../../harness/phases/01-setup.md). Read it and follow it.

## Non-negotiable constraints

1. **Inventory before interviewing.** Read `README.md`, `package.json`/`go.mod`/etc., `.github/workflows/`, any existing `AGENTS.md` / `CLAUDE.md`. Ask only what the inventory can't answer.
2. **Populate `init.sh` with real commands, not guesses.** If unsure, ask. A broken `init.sh` breaks every later phase.
3. **Verify `init.sh` boots cleanly** — run it and confirm exit 0 before finishing.
4. **Do not invent features** for `features.json`. Empty is fine.
5. **Merge, don't overwrite** existing `AGENTS.md` / `CLAUDE.md`. They may contain project-specific content.
6. **No planning.** `/setup` is pure scaffolding. Planning is the `/plan` workflow.
7. **Dispatch the `documenter` skill** after boot verification to populate the `wiki/` seed pages (`Getting-Started`, `Runbook`, `Product-Intent`, `Overview`, `Home`, `_Sidebar`) from the inventory and interview. Resolve any `OPEN QUESTIONS` before finishing.
8. **Offer GitHub Project creation** (user-scoped, `gh project create --owner @me`) — ask first. If accepted, write `.harness/project.json`.

Start by reading what's in the project now, then interview briefly on anything the inventory didn't settle.
