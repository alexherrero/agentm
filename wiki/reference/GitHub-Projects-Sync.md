<!-- mode: reference -->
# GitHub Projects board sync (agentm side)

The board-sync integration lets every phase command file "remember for later" work to a GitHub Project without leaving the session. agentm owns the config it reads, when phases emit an update, and how the whole thing stays a silent no-op when it isn't wired up. The plugin internals (templates, the write path, the type taxonomy, the drift gate) live in the crickets [github-projects reference](https://github.com/alexherrero/crickets/wiki/GitHub-Projects).

## ⚡ Quick reference

| Question | Answer |
|---|---|
| Where does the linkage live? | The `github` block on `.harness/project.json` — see [Project config](Project-Config). |
| What does agentm read from it? | `owner`, `number`, `url`, `repo`. |
| What writes it? | The `/setup` opt-in, once — it runs `gh project create` then `gh project link` and records the result. |
| Which phases emit board updates? | `/plan`, `/work`, `/release` (and `/bugfix` on the Issues side). `/review` reports, it doesn't touch the board. |
| What triggers a proposal? | Deferred work — out-of-scope items noticed in `/plan`, adjacent bugs/refactors/coverage gaps noticed while building in `/work`, cross-session themes in `/release`. |
| Does anything write to GitHub without asking? | The one-time `/setup` project linkage is preview-and-ask. Per-item deferrals during `/plan`/`/work`/`/release` are not — they're deterministic and idempotent, so the recoverability gate treats them as announce-then-proceed, the same as any other recoverable action. |
| What happens when it isn't set up? | Silent skip. No `project.json`, no `gh`, or nothing to defer → no prompt, no error. |

## The `github` block agentm reads

The linkage between this repo and its GitHub Project is a single block on `.harness/project.json`. The board sync reads four keys from it:

| Key | Type | Meaning |
|---|---|---|
| `owner` | string | The GitHub owner (user or org) that owns the Project. |
| `number` | integer | The Project number. |
| `url` | string | The Project URL — link bases are built from it. |
| `repo` | string | The repo the Project's issues live under. |

The block is additive and sits beside the enablement keys; the enablement merge-writer preserves it verbatim. The whole schema is documented in [Project config](Project-Config). The *presence* of this block is the signal every downstream phase uses to decide whether to run the board sync at all — its absence means skip.

## How opt-in writes the block

The linkage is created once, at `/setup`, and only if the operator says yes. On yes, the agent runs the two-step ProjectsV2 dance — `gh project create` then `gh project link`. Both steps are needed because ProjectsV2 has no repo-owned form: to make a project show up under the repo you link a user- or org-owned project to it. On success the flow records `{owner, number, url, repo}` onto `project.json`. An operator who declines gets no project and no config, and every phase below then skips silently.

## How the phase commands emit updates

Each phase proposes deferred work from a different signal. There's no numeric soft cap on any phase — every out-of-scope/deferred item it notices gets recorded onto the board as a `Backlog-item` or `Idea`, unconditionally.

| Phase | What triggers a proposal |
|---|---|
| `/plan` | Items in the plan's `## Out of scope` that read as deferred, not rejected |
| `/work` | Adjacent bugs, refactors, or coverage gaps noticed while implementing — not task follow-ups |
| `/release` | Cross-session themes that emerged from this release cycle |

`/review` reports findings; it does not write to the board at all. `/bugfix` is the parallel surface: it carries bugs through a full lifecycle as GitHub Issues, not as single Project items. The two surfaces are parallel, not overlapping — Projects for deferred work, Issues for bugs.

The same wiring lands identically on both supported hosts, [Claude Code and Antigravity](Compatibility), so the behavior travels with the operator.

## Announce-then-proceed, not preview-and-ask, for per-item writes

Per-item board writes during `/plan`/`/work`/`/release` are deterministic and idempotent, so they follow the recoverability gate's usual posture for that class of action: announce what's being recorded, then proceed — not stop and wait for confirmation. The write itself runs `gh issue create` + `gh project item-add`, never a raw `gh project item-create`. The one-time `/setup` project-linkage step is the genuinely preview-and-ask decision here: it is a one-time choice to *have* a board at all, not standing permission for every later write — but once that choice is made, the per-item writes it enables don't re-ask each time.

## Graceful-skip behavior

Every phase's board-sync block opens with the same check, and skips with no prompt when any of these hold:

- **No `project.json`** (or no `github` block on it) — the repo was never opted in.
- **No `gh` on the PATH** — the CLI the sync needs isn't installed.
- **Nothing deferred this session** — a phase with nothing to propose doesn't ask.

Skip means quiet: no error, no nag, no behavior change for an operator who never wanted a board. The board sync is opt-in first and silent by default.

## Related

- [Project config](Project-Config) — the `project.json` schema, including the `github` block agentm reads.
- [GitHub Projects integration](GitHub-Projects-Integration) — the *why*: opt-in, preview-and-ask, and the symmetry with `/bugfix` Issues.
- [Compatibility](Compatibility) — how the same wiring reaches both hosts.
- [github-projects plugin reference (crickets)](https://github.com/alexherrero/crickets/wiki/GitHub-Projects) — the plugin internals: templates, the one-way write path, the type taxonomy, and the drift gate.

[Reference](Reference) · [Architecture](Architecture) · [Home](Home)
