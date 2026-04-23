# First reference

The canonical lookup surface for this project — commands, configuration, entry points, exit codes. The `/setup` phase seeds the tables below from the code's public surface (CLI flags, config keys, APIs); until then, the rows are placeholders.

## ⚡ Quick Reference

| Item | Value |
|---|---|
| Project name | _Populated by `/setup` from the repo root._ |
| Runtime | _Populated by `/setup` from manifests._ |
| Entry point | _Populated by `/setup` from the project layout._ |
| Where to run tests | _Populated by `/setup` from the test config._ |

## Commands

| Command | Effect |
|---|---|
| _(populated by `/setup` from the project's CLI surface)_ | _(populated by `/setup`)_ |

## Configuration

| Key | Default | Purpose |
|---|---|---|
| _(populated by `/setup` from the project's config surface)_ | — | _(populated by `/setup`)_ |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| non-zero | Failure — inspect stderr or logs for the specific condition |

## Files

| Path | Purpose |
|---|---|
| _(populated by `/setup` from the project layout)_ | _(populated by `/setup`)_ |

## Related

- [Getting started](01-Getting-Started) — tutorial walk-through of the above surface.
- [First how-to](First-How-To) — a task-oriented recipe that uses this reference.
- [First explanation](First-Explanation) — the intent and trade-offs behind these choices.
