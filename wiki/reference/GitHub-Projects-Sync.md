<!-- mode: reference -->
# GitHub Projects board sync (agentm side)

The board-sync integration lets every phase command file "remember for later" work to a GitHub Project without leaving the session. The agentm engine owns the config it reads. It controls when phases emit an update. It also guarantees the flow stays a silent no-op when you have not wired it up. The plugin internals (templates, the write path, the type taxonomy, the drift gate) live in the crickets [github-projects reference](https://github.com/alexherrero/crickets/wiki/GitHub-Projects).

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

This block is additive. It sits beside the enablement keys. The enablement merge-writer preserves it verbatim. The entire schema is documented in [Project config](Project-Config). The *presence* of this block signals every downstream phase to run the board sync. Its absence tells them to skip it.

## How opt-in writes the block

The linkage is created once at `/setup`. It only happens if you say yes. On yes, the agent runs a two-step ProjectsV2 sequence. It runs `gh project create`. Then it runs `gh project link`. Both steps are required. ProjectsV2 has no repo-owned form. You must link a user-owned or org-owned project to your repo to make it show up there. On success, the flow records `{owner, number, url, repo}` onto `project.json`. If you decline, you get no project and no config. Every phase below will then skip silently.

## How the phase commands emit updates

Each phase proposes deferred work from a different signal. You will not hit a numeric soft cap on any phase. Every out-of-scope or deferred item it notices gets recorded onto the board. These are recorded as a `Backlog-item` or an `Idea` unconditionally.

| Phase | What triggers a proposal |
|---|---|
| `/plan` | Items in the plan's `## Out of scope` that read as deferred, not rejected |
| `/work` | Adjacent bugs, refactors, or coverage gaps noticed while implementing — not task follow-ups |
| `/release` | Cross-session themes that emerged from this release cycle |

The `/review` phase reports findings. It does not write to the board at all. The `/bugfix` phase acts as the parallel surface. It carries bugs through a full lifecycle as GitHub Issues, not as single Project items. The two surfaces are parallel and do not overlap. You use Projects for deferred work. You use Issues for bugs.

The same wiring lands identically on both supported hosts, [Claude Code and Antigravity](Compatibility). The behavior travels with you.

## Announce-then-proceed, not preview-and-ask, for per-item writes

Per-item board writes during `/plan`/`/work`/`/release` are deterministic and idempotent. They follow the recoverability gate's usual posture for that class of action. The agent will announce what it is recording. Then it will proceed. It will not stop and wait for your confirmation. The write itself runs `gh issue create` followed by `gh project item-add`. It never runs a raw `gh project item-create`. 

The `/setup` project-linkage step acts as the genuine preview-and-ask decision here. It is a one-time choice to *have* a board at all. It is not standing permission for every later write. Once you make that choice, the per-item writes it enables do not re-ask you each time.

## Graceful-skip behavior

Every phase's board-sync block opens with the same check. It skips with no prompt when any of these conditions hold:

- **No `project.json`** (or no `github` block on it) — you never opted the repo in.
- **No `gh` on the PATH** — you do not have the needed CLI installed.
- **Nothing deferred this session** — a phase with nothing to propose does not ask.

Skip means quiet. You see no error. You see no nag. You experience no behavior change if you never wanted a board. The board sync is opt-in first. It is silent by default.

## Related

- [Project config](Project-Config) — the `project.json` schema, including the `github` block agentm reads.
- [GitHub Projects integration](GitHub-Projects-Integration) — the *why*: opt-in, preview-and-ask, and the symmetry with `/bugfix` Issues.
- [Compatibility](Compatibility) — how the same wiring reaches both hosts.
- [github-projects plugin reference (crickets)](https://github.com/alexherrero/crickets/wiki/GitHub-Projects) — the plugin internals: templates, the one-way write path, the type taxonomy, and the drift gate.

[Reference](Reference) · [Architecture](Architecture) · [Home](Home)
