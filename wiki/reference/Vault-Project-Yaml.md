<!-- mode: reference -->
# Vault project.yaml reference

> [!NOTE]
> **Status: implemented** — shipped by `tasks/176-converge-the-vault-layout` (step 5). Every one of the 12 live vault projects carries a `project.yaml`; `check-project-yaml` reads 0 findings across 12 of 12 as of 2026-09-25.

Every vault project's root carries a `project.yaml` — the grounding config a session reads to tell which project it is in and what that project touches, without parsing prose. It is one of the five files the project root is locked to; see [Memory daemon reference § The project root locks to five files](Memory-Daemon#the-project-root-locks-to-five-files).

## ⚡ Quick Reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `slug` | string | yes | The project's directory name. `check-project-yaml` fails the file when this doesn't match (`scripts/check-project-yaml.py:79-80`). |
| `title` | string | yes | The project's human-readable name; must be non-empty (`:81-82`). |
| `status` | string | yes | One of `queued`, `active`, `parked`, `done`, `dropped` (`:53`, `:83-84`). |
| `repositories` | list of `owner/repo` | yes | The GitHub repos this project touches. May be empty (`:54`, `:85-92`). |
| `code_paths` | list of `~/…` strings | yes | Home-relative paths into those repos, for wherever the code actually lives. May be empty (`:93-100`). |
| `board` | mapping (`owner`, `number`) | no | The GitHub Project this project syncs to, when one exists — exactly `owner` (string) and a positive `number`, nothing else (`:101-107`). |
| `sensitivity` | string | no | One lowercase word, e.g. `personal-financial` (used by the `home` project) — flags data the operator wants handled carefully (`:55`, `:108-109`). |
| Where's the template? | — | — | `standards/templates/project.yaml` — seeds every new project's copy, minus `slug`. `standards/templates/` is in `recall_exempt_areas`, so the template is never indexed or served by recall; it's a shape to copy, never content to surface. |
| What checks it? | — | — | `check-project-yaml` — see [CI gates reference](CI-Gates). |
| Related pages | — | — | [Memory daemon reference](Memory-Daemon), [CI gates reference](CI-Gates), [Project config reference](Project-Config) (a different file — the *harness's* `.harness/project.json`) |

## Schema

`project.yaml` parses as a YAML mapping. Any key outside the required and optional sets below is a finding (`scripts/check-project-yaml.py:76-78`) — a typo in a key name is caught rather than silently ignored.

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
```

`standards/templates/project.yaml` is checked the same way, except it carries no `slug` — the one field a template can't have, since it names the directory the template itself isn't in (`scripts/check-project-yaml.py:72`).

## Related

- [Memory daemon reference](Memory-Daemon) — the space table and the door's five-file project-root lock this schema fits into.
- [CI gates reference](CI-Gates) — the `check-project-yaml` gate that validates every project's copy.
- [Project config reference](Project-Config) — the harness's own `.harness/project.json`. A sibling concept, not the same file: that one is per-repo enablement config; this one is per-vault-project grounding config.
