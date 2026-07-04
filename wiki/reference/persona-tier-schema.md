# Persona tier — schema and gate reference

> [!NOTE]
> **Status: implemented** — V5-12 shipped the primitive (tasks 1–3): gate (`scripts/check-personas.py`), degenerate persona (`personas/rememberer.md`), CI on Linux/macOS/Windows. V5-11 shipped the first real composed persona (`personas/team-coordinator.md`): `requires: [queue_status_lite]`, `enhances: [developer-workflows, github-projects]`.

This page documents the `kind: persona` manifest primitive and the `check-personas` static gate.

## ⚡ Quick Reference

| Field | Type | Required | Constraint |
|---|---|---|---|
| `kind` | string | yes | Must be `"persona"` |
| `name` | string | yes | Slug identifying the persona |
| `requires` | list\[string\] | yes | Must be a subset of substrate-native stems (files in `scripts/`) |
| `enhances` | list\[string\] | yes | Any capability name(s); unmet entries are not errors |
| `always_load` | bool | no | Must be absent or `false`; `true` is a gate violation |

| Gate | Script | Invocation | Exit 0 when |
|---|---|---|---|
| `check-personas` | `scripts/check-personas.py` | `python3 scripts/check-personas.py [--root DIR]` | All personas pass both assertions |

## `kind: persona` manifest shape

Every file under `personas/*.md` must open with a YAML frontmatter block delimited by `---`. Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `kind` | yes | string | Must be exactly `"persona"`. |
| `name` | yes | string | Slug identifying this persona (e.g. `rememberer`). |
| `requires` | yes | list[string] | Hard substrate deps. Every entry must be the stem of a file in `scripts/` (`<stem>.py` or `<stem>.sh`). Empty list is valid. |
| `enhances` | yes | list[string] | Soft capability deps. Any capability name is accepted; unmet entries are not errors. See [Soft-Composition](Soft-Composition). |
| `always_load` | no | bool | Must be absent or `false`. Setting to `true` is a gate violation (see the [persona-tier design](persona-tier)). Both spellings (`always_load` and `always-load`) are checked. |

### Example: the degenerate persona (`personas/rememberer.md`)

```yaml
---
kind: persona
name: rememberer
requires: []
enhances: []
description: >
  The standing concern that recalls, reflects, and curates the operator's memory
  across sessions. Anchors on the neutral substrate; composes no external capabilities.
---
```

### Example: the first real composed persona (`personas/team-coordinator.md`)

Shipped in V5-11. Hard-requires `queue_status_lite` (agentm-native); soft-composes `developer-workflows` and `github-projects` via `enhances:`.

```yaml
---
kind: persona
name: team-coordinator
requires: [queue_status_lite]
enhances: [developer-workflows, github-projects]
description: >
  The standing concern for multi-worker program coordination: surfaces plan standup,
  readiness, and merge order. Degrades gracefully when developer-workflows or
  github-projects are absent.
---
```

The `requires: [queue_status_lite]` entry passes `check-personas` because `scripts/queue_status_lite.py` is a substrate-native script (agentm-shipped). The `enhances:` entries are never validated by `check-personas` — unmet soft deps are not errors.

## `check-personas` gate

**Script:** `scripts/check-personas.py`

**Invocation:**

```bash
python3 scripts/check-personas.py [--root DIR]
```

`--root DIR` sets the repo root (default: parent of the script's own directory). Runs on every `*.md` file under `<root>/personas/`.

### Assertions

The gate enforces two invariants (both in the `_check_one()` function in `scripts/check-personas.py`):

1. **`requires ⊆ substrate-native`** — each `requires:` entry must match `scripts/<entry>.py` or `scripts/<entry>.sh`. This mechanically prevents personas from hard-depending on crickets capabilities (no file under `scripts/` exists for them).

2. **No always-load** — `always_load: true` (or `always-load: true`) is rejected. Personas are on-demand; an always-load persona inflates the per-call token floor (issue #46).

`kind: persona` is also asserted; a wrong `kind:` value is a violation.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All personas pass both invariants. |
| `1` | One or more violations found. Violations printed to stderr. |
| `2` | Setup error: `personas/` not found, YAML parse failure, or unknown argument. |

### Gate placement

`check-personas` appears as one line in `scripts/check-all.sh` and is wired into `.github/workflows/tests-linux.yml`, `tests-mac.yml`, and `tests-windows.yml` (task 3). It runs after `check-wiki.py` in the battery ordering.

### Unit tests

`scripts/test_check_personas.py` — 11 tests across four classes:

| Class | Tests |
|---|---|
| `TestPass` | rememberer-like, valid substrate requires, no personas dir, empty dir, real tree |
| `TestRejectNonSubstrateRequires` | crickets capability rejected, unknown name rejected |
| `TestRejectAlwaysLoad` | `always_load` underscore rejected, `always-load` hyphen rejected |
| `TestMiscValidation` | wrong `kind:` rejected, `.sh` stem accepted |

## Related

- [Soft-Composition](Soft-Composition) — `enhances:` field that personas reuse for optional composition.
- [Persona tier design](persona-tier) — design decisions: DC-2 (positive-match kind), DC-3 (enhances: reuse), DC-4 (no-always-load invariant).
- [Capability-Resolver](Capability-Resolver) — the resolver that backs `enhances:` lookups.
- [CI-Gates](CI-Gates) — where `check-personas` appears in the gate battery.
