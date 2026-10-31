<!-- mode: reference -->
# Vault project.yaml reference

> [!NOTE]
> **Status: implemented** — shipped by `tasks/176-converge-the-vault-layout` (step 5). Every one of the 12 live vault projects carries a `project.yaml`; `check-project-yaml` reads 0 findings across 12 of 12 as of 2026-09-25. Three optional keys came after it: `former_names` (task 186), `bare_issue_floor` (task 187) and `map_folders` (task 193).

Every vault project's root carries a `project.yaml` — the grounding config a session reads to tell which project it is in and what that project touches, without parsing prose. It is one of the five files the project root is locked to; see [Memory daemon reference § The project root locks to five files](Memory-Daemon#the-project-root-locks-to-five-files).

## ⚡ Quick Reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `slug` | string | yes | The project's directory name. `check-project-yaml` fails the file when this doesn't match (`scripts/check-project-yaml.py:86-87`). |
| `title` | string | yes | The project's human-readable name; must be non-empty (`:85-86`). |
| `status` | string | yes | One of `queued`, `active`, `parked`, `done`, `dropped` (`:57`, `:87-88`). |
| `repositories` | list of `owner/repo` | yes | The GitHub repos this project touches. May be empty (`:58`, `:89-96`). |
| `code_paths` | list of `~/…` strings | yes | Home-relative paths into those repos, for wherever the code actually lives. May be empty (`:97-104`). |
| `board` | mapping (`owner`, `number`) | no | The GitHub Project this project syncs to, when one exists — exactly `owner` (string) and a positive `number`, nothing else (`:105-111`). |
| `sensitivity` | string | no | One lowercase word, e.g. `personal-financial` (used by the `home` project) — flags data the operator wants handled carefully (`:59`, `:112-113`). |
| `former_names` | list of `owner/repo` | no | The names a renamed repository used to have, for a project that lists exactly one repository (`:114-123`). The entity builder folds each old name into the current repository's page, so a note that still cites the old name counts toward it (task 186). |
| `bare_issue_floor` | positive whole number | no | Where the repository's issue numbers start, for a project that lists at least one repository (`:124-129`). A bare `#NN` below it stays unqualified, `issue:#NN`; see [Bare issue floor](#bare-issue-floor) (task 187). |
| `map_folders` | list of project-relative folders | no | Folders the project's map lists beyond `research/`, `drafts/` and `desk/`, each a plain path with no `..` and no hidden or `_`-prefixed part (`check-project-yaml`). See [Map folders](#map-folders) (task 193). |
| `recall_excludes` | list of project slugs | no | The projects whose notes this project's sessions must not be served. See [Recall excludes](#recall-excludes). |
| Where's the template? | — | — | `standards/templates/project.yaml` — seeds every new project's copy, minus `slug`. `standards/templates/` is in `recall_exempt_areas`, so the template is never indexed or served by recall; it's a shape to copy, never content to surface. |
| What checks it? | — | — | `check-project-yaml` — see [CI gates reference](CI-Gates). |
| Related pages | — | — | [Memory daemon reference](Memory-Daemon), [CI gates reference](CI-Gates), [Project config reference](Project-Config) (a different file — the *harness's* `.harness/project.json`) |

## Schema

`project.yaml` parses as a YAML mapping. Any key outside the required and optional sets below is a finding (`scripts/check-project-yaml.py:83-85`) — a typo in a key name is caught rather than silently ignored.

```yaml
slug: example-project
title: Example Project
status: active
repositories:
  - owner/repo
code_paths:
  - ~/code/repo
board:
  owner: owner
  number: 2
sensitivity: personal-financial
former_names:
  - owner/old-repo-name
bare_issue_floor: 48
map_folders:
  - analysis
recall_excludes:
  - other-project
```

`standards/templates/project.yaml` is checked the same way, except it carries no `slug` — the one field a template can't have, since it names the directory the template itself isn't in (`scripts/check-project-yaml.py:79`).

## Recall excludes

`recall_excludes` keeps one project's notes out of another's sessions, for a project that must not see them however relevant a search finds them. Without it, a project's notes only rank a little lower in another project's session. With it, they are not served at all. Everything else stays: the session keeps the rest of its recall.

```yaml
recall_excludes:
  - other-project
```

**Who it applies to.** A session works on the project when its folder is under one of the project's `code_paths`, or under the project's own folder in the vault (`projects/<slug>/`, which `code_paths` doesn't list), or in a linked git worktree of either, wherever that worktree sits. Every other session, and a session working on the excluded project itself, sees no change.

**What it keeps out.** A note under `projects/<slug>/` or `projects/completed/<slug>/`, and a note anywhere whose `project:` names the slug. A note elsewhere that only mentions the project's subject is not covered.

**Where it holds.**

| Door | How it knows the folder | If the rule can't be read |
|---|---|---|
| The prompt hook | the payload's `cwd`, else its own folder | no recall that prompt; stderr says why |
| The session-start hook | the payload's `cwd`, else its own folder | entries that carry a project are withheld; the standing rules still load; stderr says why |
| `/memory search`, `recall.py query`, `/memory search --deep` | the folder it runs in | prints the reason and exits 1 |
| `agentmd search` | `-cwd`, else the folder it runs in | prints the reason and exits non-zero |
| `memory_search` over MCP | the `cwd` argument | a tool error naming the reason |

`memory_search` is the odd one. The daemon is one resident process that can't see who is calling, so the tool asks for `cwd`. A call with no `cwd`, or a relative one, is held to every project's rule, because it may be a session the rule covers. That caller gets a note saying so, and passing `cwd` gets the notes back.

**What it doesn't stop.** It governs recall, not the filesystem. A session can still open the files with `Read` or `grep` the vault, and `agentmd search` run from a folder outside the project isn't under the rule. Pulling a project's notes out of a session's reach would take moving them, which is a different job.

**Unreadable means withheld.** A rule that isn't a list, or that names a project the vault doesn't have, fails closed for that project's sessions. A `project.yaml` that won't read at all fails closed for its own sessions and for any folder no other project claims, since it may be the file that claims it. Sessions of projects that do read are not held up. `check-project-yaml` catches the first two before a session does: an entry must be a slug, must not be the project itself, and must name a project under `projects/` or `projects/completed/`.

## Bare issue floor

A repository whose roadmap numbered its items in the same range as its first GitHub issues makes a bare `#15` in an old note mean either one. `bare_issue_floor` says where the issues start: set it one above the roadmap's last item, so `48` for a roadmap that ran to item 47. Below the floor the extractor leaves a bare number unqualified, as `issue:#NN`. The note is still found by it in search, but no issue page is built from it.

| Written as | Below the floor | At or above it |
|---|---|---|
| `#NN`, `(#NN)` | stays `issue:#NN` | qualifies to the note's repository |
| `crickets #NN` (a repository's short name first) | stays `issue:#NN` | qualifies to that repository |
| `PR #NN`, `issue #NN`, `pull request #NN` | qualifies | qualifies |
| `owner/repo#NN`, a GitHub issue or pull request address | qualifies | qualifies |

The floor belongs to a repository, not to a note. A project's floor applies to each repository it lists, a repository two projects list takes the higher of the two floors, and one repository's floor says nothing about another's. The daemon's reader (`projectbind.IssueFloors`, `daemon/internal/projectbind/projectbind.go:190`) gives no floor for a value that is not a positive whole number. The gate is stricter: it also refuses a bool or a quoted number, and a floor on a project that lists no repository.

The floors are part of the index's recorded extractor version (`extractorVersion`, `daemon/internal/index/entityreextract.go:35`). Setting, raising or removing a floor therefore re-derives every indexed note's entity rows on the daemon's next open, with no reindex. How the extractor reads a reference is in [Memory daemon reference § Entities, and the pages built from them](Memory-Daemon#entities-and-the-pages-built-from-them).

## Map folders

A project's generated map, `moc-<slug>.md`, has a section for each of the skeleton's working folders: `## Research`, `## Drafts` and `## Desk`. `map_folders` is how a project asks for more. Each entry is a folder relative to the project's root, such as `analysis` or `research/vault-perfection`, and the map gives it a `## <folder>/` section after the three skeleton ones, in the order the list names them.

| Rule | What happens |
|---|---|
| Opt-in only | A folder outside the skeleton is listed only when it is named here. `projects/movies-tv-games/cleanup/` holds over 1,300 manifests, and listing it should be the operator's call, not the night's. |
| What the section holds | The folder's own loose notes first, then each subfolder. A group of 12 notes or fewer is listed note by note, newest `created` first. A larger group gets its own generated `moc-<folder>.md` beside its notes, and the project map links that page on one line. See [MOC generator reference](MOC-Generator#what-replaced-the-per-kind-pages). |
| Entries the daemon ignores | `projectbind.MapFolders` (`daemon/internal/projectbind/projectbind.go`) returns the list as written. The map's reader drops an entry that is `..`, absolute, hidden or `_`-prefixed, or that names a folder the map already sections (`tasks`, `completed`, `decisions`, `designs`). |
| What the gate checks | `check-project-yaml` requires a list, and flags an entry that is not a plain project-relative folder. A malformed entry is a finding rather than a silent skip. |

## Related

- [Memory daemon reference](Memory-Daemon) — the space table and the door's five-file project-root lock this schema fits into.
- [CI gates reference](CI-Gates) — the `check-project-yaml` gate that validates every project's copy.
- [Project config reference](Project-Config) — the harness's own `.harness/project.json`. A sibling concept, not the same file: that one is per-repo enablement config; this one is per-vault-project grounding config.
