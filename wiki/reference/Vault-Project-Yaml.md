<!-- mode: reference -->
# Vault project.yaml reference

> [!NOTE]
> **Status: pending** — planned by `tasks/176-converge-the-vault-layout` (step 5). `project.yaml` does not exist in the vault yet. This page describes the schema the plan locks in, seeded from `standards/templates/project.yaml`.

Every vault project's root carries a `project.yaml` — the grounding config a session reads to tell which project it is in and what that project touches, without parsing prose. It is one of the five files the project root is locked to; see [Memory daemon reference § The project root locks to five files](Memory-Daemon#the-project-root-locks-to-five-files).

## ⚡ Quick Reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `slug` | string | yes | The project's directory name. `check-project-yaml` fails the file when this doesn't match. |
| `title` | string | yes | The project's human-readable name. |
| `status` | string | yes | The project's lifecycle state. The plan does not fix the full vocabulary yet. |
| `repositories` | list of `owner/repo` | yes | The GitHub repos this project touches. |
| `code_paths` | list of strings | yes | Home-relative paths into those repos, for wherever the code actually lives. |
| `board` | object (`owner`, `number`) | no | The GitHub Project this project syncs to, when one exists. |
| `sensitivity` | string | no | A marking like `personal-financial` (the plan names this one, for the `home` project) — flags data the operator wants handled carefully. |
| Where's the template? | — | — | `standards/templates/project.yaml` (pending — step 5 seeds it, alongside `charter.md`, `blueprint.md`, and a `tracker.md` template carrying a note that the tracker is generated, not hand-written). |
| What checks it? | — | — | `check-project-yaml` (pending) — see [CI gates reference](CI-Gates). |
| Related pages | — | — | [Memory daemon reference](Memory-Daemon), [CI gates reference](CI-Gates), [Project config reference](Project-Config) (a different file — the *harness's* `.harness/project.json`) |

## Schema

_The field list above is locked by the plan's step 5. The exact YAML shape (list vs. mapping for `repositories`, the full `status` vocabulary) is not yet written — filled in once step 5 ships._

## Related

- [Memory daemon reference](Memory-Daemon) — the space table and the door's five-file project-root lock this schema fits into.
- [CI gates reference](CI-Gates) — the `check-project-yaml` gate that validates every project's copy.
- [Project config reference](Project-Config) — the harness's own `.harness/project.json`. A sibling concept, not the same file: that one is per-repo enablement config; this one is per-vault-project grounding config.
