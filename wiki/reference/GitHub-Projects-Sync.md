<!-- mode: reference -->
# GitHub Projects board sync (agentm side)

The board-sync integration lets every phase command file "remember for later" work to a GitHub Project without leaving the session. agentm owns the config it reads, when phases emit an update, and how the whole thing stays a silent no-op when it isn't wired up. The plugin internals (templates, the write path, the type taxonomy, the drift gate) live in the crickets [github-projects reference](https://github.com/alexherrero/crickets/wiki/GitHub-Projects).

## ⚡ Quick reference

| Question | Answer |
|---|---|
| Where does the linkage live? | The `github` block on `.harness/project.json` — see [Project config](Project-Config). |
| What does agentm read from it? | `owner`, `number`, `url`, `repo`. |
| What writes it? | The `/setup` opt-in, once — it runs `gh project create` then `gh project link` and records the result. |
| Which phases emit board updates? | `/plan`, `/work`, `/review`, `/release` (and `/bugfix` on the Issues side). |
| What triggers a proposal? | Deferred work — out-of-scope items, adjacent bugs noticed while building, deferred review findings, release-cycle themes. |
| Does anything write to GitHub without asking? | No. Every write is preview-and-ask — the operator sees the exact title and body first. |
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

Each phase proposes deferred work from a different signal, with a soft cap that flags scope-creep rather than blocking. The proposal is always previewed and confirmed before any `gh` call runs.

| Phase | What triggers a proposal | Soft reminder |
|---|---|---|
| `/plan` | Items in the plan's `## Out of scope` that read as deferred, not rejected | more than 5 proposals → rethink whether some are in-scope tasks |
| `/work` | Adjacent bugs, refactors, or coverage gaps noticed while implementing — not task follow-ups | more than 3 → probably scope-creeping, not deferring |
| `/review` | Findings the operator elects to defer rather than block on | no hard cap — five real deferrals, propose five |
| `/release` | Cross-session themes that emerged from this release cycle | higher bar — a theme is a pattern, not a single item |

`/bugfix` is the parallel surface: it carries bugs through a full lifecycle as GitHub Issues, not as single Project items. The two surfaces are parallel, not overlapping — Projects for deferred work, Issues for bugs.

The same wiring lands identically on both supported hosts, [Claude Code and Antigravity](Compatibility), so the behavior travels with the operator.

## Preview-and-ask, always

No `gh project item-create` ever runs without the operator first seeing the exact title and body and confirming. A `gh` call without that confirmation is a contract violation. The opt-in at `/setup` is a one-time decision to *have* a board; it is not standing permission to write to it unattended.

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
