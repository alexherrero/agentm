# agentic-harness

A small, opinionated harness for doing production-quality engineering with AI coding agents — Claude Code, Antigravity, Codex, Gemini CLI, and any tool that reads `AGENTS.md`. Six phase-gated slash commands, two sub-agents, deterministic verification, on-disk state. This wiki is the project's own documentation, maintained by the `documenter` sub-agent at phase boundaries.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What is this repo? | [Product-Intent](Product-Intent) |
| How do I install it into a project? | [Getting-Started](Getting-Started) |
| How do I cut a release? | [Runbook](Runbook) — "Cutting a release" |
| How does `install.sh --update` behave? | [Runbook](Runbook) — "Updating an installed harness" |
| Where does the code live? | [Overview](Overview) |
| Why phase-gated? | [ADR 0001](0001-phase-gated-workflow) |
| Why phase-boundary docs (not inline)? | [ADR 0002](0002-documentation-convention) |
| Why ProjectsV2 create + link (not repo-owned)? | [ADR 0003](0003-ProjectsV2-Ownership-And-Linking) |
| What shipped recently? | [Completed-Features](Completed-Features) |

## 🛠 Development

Build, install, and contribute to the harness itself.

- [Getting-Started](Getting-Started) — install the harness into a target project; run the local test suite.
- [Completed-Features](Completed-Features) — reverse-chronological log of what's shipped, maintained at `/release`.

## 📟 Operational

Run, release, and maintain the harness.

- [Runbook](Runbook) — `install.sh --update`, cutting a release with `ship-release`, CI gates, the dogfood-freshness check.

## 🎨 Design

What the harness is for, who it's for, and why it's shaped the way it is.

- [Product-Intent](Product-Intent) — problem statement, target user, non-goals.
- [GitHub-Projects-Integration](GitHub-Projects-Integration) — preview-and-ask `gh project item-create` from every phase; opt-in at `/setup`.

## 🏗 Architecture

How the pieces fit together.

- [Overview](Overview) — repo layout: `harness/` (canonical specs), `adapters/` (per-tool shims), `templates/` (what `install.sh` drops into a project), `scripts/` (test infra, never propagated).
- [ADR 0001: Phase-gated workflow](0001-phase-gated-workflow) — why `setup → plan → work → review → release` with hard boundaries and one task per `/work` session.
- [ADR 0002: Documentation convention](0002-documentation-convention) — why docs live in `wiki/`, are written by a sub-agent at phase boundaries, and why the installer boundary keeps this repo's own `wiki/` out of target projects.
- [ADR 0003: ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking) — why `/setup` runs a two-step `gh project create` + `gh project link --repo` flow rather than assuming a repo-owned form.
