<!-- mode: reference -->
# Vault project.yaml reference

> [!NOTE]
> **Status: implemented** — shipped by `tasks/176-converge-the-vault-layout` (step 5). Every one of the 12 live vault projects carries a `project.yaml`; `check-project-yaml` reads 0 findings across 12 of 12 as of 2026-09-25. Two optional keys came after it: `former_names` (task 186) and `bare_issue_floor` (task 187).

Every vault project's root carries a `project.yaml` — the grounding config a session reads to tell which project it is in and what that project touches, without parsing prose. It is one of the five files the project root is locked to; see [Memory daemon reference § The project root locks to five files](Memory-Daemon#the-project-root-locks-to-five-files).

## ⚡ Quick Reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `slug` | string | yes | The project's directory name. `check-project-yaml` fails the file when this doesn't match (`scripts/check-project-yaml.py:83-84`). |
| `title` | string | yes | The project's human-readable name; must be non-empty (`:85-86`). |
| `status` | string | yes | One of `queued`, `active`, `parked`, `done`, `dropped` (`:57`, `:87-88`). |
| `repositories` | list of `owner/repo` | yes | The GitHub repos this project touches. May be empty (`:58`, `:89-96`). |
| `code_paths` | list of `~/…` strings | yes | Home-relative paths into those repos, for wherever the code actually lives. May be empty (`:97-104`). |
| `board` | mapping (`owner`, `number`) | no | The GitHub Project this project syncs to, when one exists — exactly `owner` (string) and a positive `number`, nothing else (`:105-111`). |
| `sensitivity` | string | no | One lowercase word, e.g. `personal-financial` (used by the `home` project) — flags data the operator wants handled carefully (`:59`, `:112-113`). |
| `former_names` | list of `owner/repo` | no | The names a renamed repository used to have, for a project that lists exactly one repository (`:114-123`). The entity builder folds each old name into the current repository's page, so a note that still cites the old name counts toward it (task 186). |
| `bare_issue_floor` | positive whole number | no | Where the repository's issue numbers start, for a project that lists at least one repository (`:124-129`). A bare `#NN` below it stays unqualified, `issue:#NN`; see [Bare issue floor](#bare-issue-floor) (task 187). |
| Where's the template? | — | — | `standards/templates/project.yaml` — seeds every new project's copy, minus `slug`. `standards/templates/` is in `recall_exempt_areas`, so the template is never indexed or served by recall; it's a shape to copy, never content to surface. |
| What checks it? | — | — | `check-project-yaml` — see [CI gates reference](CI-Gates). |
| Related pages | — | — | [Memory daemon reference](Memory-Daemon), [CI gates reference](CI-Gates), [Project config reference](Project-Config) (a different file — the *harness's* `.harness/project.json`) |

## Schema

`project.yaml` parses as a YAML mapping. Any key outside the required and optional sets below is a finding (`scripts/check-project-yaml.py:80-82`) — a typo in a key name is caught rather than silently ignored.

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
```

`standards/templates/project.yaml` is checked the same way, except it carries no `slug` — the one field a template can't have, since it names the directory the template itself isn't in (`scripts/check-project-yaml.py:76`).

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

## Related

- [Memory daemon reference](Memory-Daemon) — the space table and the door's five-file project-root lock this schema fits into.
- [CI gates reference](CI-Gates) — the `check-project-yaml` gate that validates every project's copy.
- [Project config reference](Project-Config) — the harness's own `.harness/project.json`. A sibling concept, not the same file: that one is per-repo enablement config; this one is per-vault-project grounding config.
