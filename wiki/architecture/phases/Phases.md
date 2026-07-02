<!-- mode: index -->
# Phases

_Six phases, hard boundaries, one per session — the discipline that keeps coherence-critical work single-threaded instead of freestyling the whole lifecycle at once._

The harness is a phase-gated workflow. Development is cut into six phases with hard boundaries between them, and a session executes **exactly one** phase — not the full lifecycle in one pass. Each phase reads the state the last one wrote and writes the state the next one needs, so the conversation can end at any boundary without losing the thread.

## How it works

The six phases, in order, with Bugfix as a parallel track for defects:

| Phase | Does | Stops when |
|---|---|---|
| **Setup** | first-time scaffold + feature list | the project is initialized (once) |
| **Plan** | brief → `PLAN.md` with tasks + verification | the plan is written; no code |
| **Work** | implement the plan's tasks, gated per task | the task list is done + gates green |
| **Review** | adversarial critique — assume bugs exist | a failing test or a line-level defect |
| **Release** | pre-merge gate: clean tree, checks pass | the changelog is updated + green |
| **Bugfix** | Report → Analyze → Fix → Verify | the fix is verified |

The canonical spec for each lives in the crickets **developer-workflows** plugin, not in agentm — the phase loop was unbundled in V5 (the [AgentM HLD](agentm-hld)); agentm provides the durable state substrate and memory engine the phases run on. The host adapters point back to those specs rather than restating them. Verification is **executable first** — typecheck, lint, tests, build come before any LLM judgement, which augments but never replaces them.

## How it fits

- **[AgentMemory](AgentMemory)** — phase state (PLAN, progress) is written to memory, not held in the conversation. State on disk is what makes one-phase-per-session safe.
- **[Host adapters](Host-Adapters)** — each host exposes the phases as its own entrypoints (slash commands on Claude Code, prompted equivalents on Antigravity).
- **[Toolkit interface ↔ crickets](Toolkit-Interface)** — crickets primitives *enhance* a phase when present and graceful-skip when not.

## See also

Detail:

- [How the pieces fit](How-The-Pieces-Fit) · [Product intent](Product-Intent) — why the workflow is shaped this way.
- [Use auto-context in harness phases](Use-Auto-Context-In-Harness-Phases) — phase recipes. Cutting a release is the crickets [Releasing Conventions](https://github.com/alexherrero/crickets/wiki/Releasing-Conventions) skill's job, not agentm's.
- [Phase-gated workflow design](agentm-hld) — the founding decision.

[Architecture](Architecture) · [Explanation](Explanation) · [Home](Home)
