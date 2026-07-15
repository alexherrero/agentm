# Persona tier — schema and gate reference

> [!NOTE]
> **Status: implemented, and grown well past its original shape.** V5-12 shipped the primitive (tasks 1–3): gate (`scripts/check-personas.py`), degenerate persona (`personas/brain.md`), CI on Linux/macOS/Windows. V5-11 shipped the first real composed persona (`personas/team-coordinator.md`). Since then, "persona-tier build-part 3 — activation pipeline" (2026-07-05) added four activation-axis fields (`tier`, `modes`, `triggers`, `opinions`) to the manifest shape and the gate, and the AG Wave D persona roster (2026-07-06) grew `personas/` from 2 files to 11.

This page documents the `kind: persona` manifest primitive and the `check-personas` static gate.

## ⚡ Quick Reference

| Field | Type | Required | Constraint |
|---|---|---|---|
| `kind` | string | yes | Must be `"persona"` |
| `name` | string | yes | Slug identifying the persona (not gate-enforced — `check-personas` has no validation logic for this key) |
| `requires` | list\[string\] | yes | Must be a subset of substrate-native stems (files in `scripts/`) |
| `enhances` | list\[string\] | yes | Any capability name(s); unmet entries are not errors (not gate-enforced either) |
| `always_load` | bool | no | Must be absent or `false`; `true` is a gate violation |
| `tier` | string | no | One of `T0`/`T1`/`T2`/`T3`/`T4` (the model+effort scale), if present |
| `modes` | list\[string\] | no | Drawn from `{sub-agent, interactive, loop, goal}`, if present |
| `triggers` | list\[string\] | no | Non-empty strings — a workflow-step name or cue string; shape only, no semantic check against real phase names |
| `opinions` | list\[string\] | no | String list — shape only; resolution to a real `opinions/<name>.md` is a runtime, graceful concern, never a gate failure |

| Gate | Script | Invocation | Exit 0 when |
|---|---|---|---|
| `check-personas` | `scripts/check-personas.py` | `python3 scripts/check-personas.py [--root DIR]` | All personas pass every invariant (or `personas/` doesn't exist — "nothing to check" is also a clean pass) |

## `kind: persona` manifest shape

Every file under `personas/*.md` must open with a YAML frontmatter block delimited by `---`. Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `kind` | yes | string | Must be exactly `"persona"`. |
| `name` | yes | string | Slug identifying this persona (e.g. `brain`). Not validated by the gate — a manifest with no `name:` at all still passes. |
| `requires` | yes | list[string] | Hard substrate deps. Every entry must be the stem of a file in `scripts/` (`<stem>.py` or `<stem>.sh`). Empty list is valid. |
| `enhances` | yes | list[string] | Soft capability deps. Any capability name is accepted; unmet entries are not errors. Not validated by the gate. See [Soft-Composition](Soft-Composition). |
| `always_load` | no | bool | Must be absent or `false`. Setting to `true` is a gate violation (see the [persona-tier design](persona-tier)). Both spellings (`always_load` and `always-load`) are checked. |
| `tier` | no | string | One of `T0`/`T1`/`T2`/`T3`/`T4`, if present. |
| `modes` | no | list[string] | Drawn from `{sub-agent, interactive, loop, goal}`, if present; an empty list is rejected (a persona with `modes: []` declares itself launchable nowhere). |
| `triggers` | no | list[string] | Non-empty strings, if present. |
| `opinions` | no | list[string] | Strings, if present — shape only, not resolved against real `opinions/` files at gate time. |

### Example: the degenerate persona (`personas/brain.md`)

```yaml
---
kind: persona
name: brain
requires: []
enhances: []
description: >
  The standing concern that recalls, reflects, and curates the operator's memory
  across sessions. Anchors on the neutral substrate (harness_memory, storage_seam);
  composes no external capabilities. The degenerate persona — zero composed plugins,
  requires ⊆ substrate — the persona agentm already shipped, now named.
tier: T0
opinions: []
modes: [sub-agent, interactive, loop, goal]
triggers: []
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
  The standing concern that turns raw multi-worker status into decision-ready
  recommendations. Reads the vault, computes the answers, and hands the operator
  a ready-to-act standup, an overlap verdict, and a merge order. Advisory only —
  zero execution authority. The first composed persona: soft-depends on
  developer-workflows (the dev-loop surface) and github-projects (the board
  display), and hard-depends on queue_status_lite (the substrate read model).
tier: T2
opinions: [how-we-engineer]
modes: [loop, sub-agent]
triggers: [team-coordinator]
---
```

The `requires: [queue_status_lite]` entry passes `check-personas` because `scripts/queue_status_lite.py` is a substrate-native script (agentm-shipped). The `enhances:` entries are never validated by `check-personas` — unmet soft deps are not errors.

### The other nine personas

`personas/` grew to 11 files in the AG Wave D roster (2026-07-06): `brain`, `team-coordinator`, and nine more — `architect`, `designer`, `engineer`, `maintainer`, `operator`, `researcher`, `reviewer`, `tech-lead`, `troubleshooter`. Run `python3 scripts/check-personas.py` for the live count and pass/fail state; it prints `"11 personas — clean."` on this repo today.

## `check-personas` gate

**Script:** `scripts/check-personas.py`

**Invocation:**

```bash
python3 scripts/check-personas.py [--root DIR]
```

`--root DIR` sets the repo root (default: parent of the script's own directory). Runs on every `*.md` file under `<root>/personas/`.

### Assertions

The gate enforces six invariants — the original two, plus four activation-axis shape checks added in "persona-tier build-part 3 — activation pipeline" (2026-07-05):

1. **`requires ⊆ substrate-native`** — each `requires:` entry must match `scripts/<entry>.py` or `scripts/<entry>.sh`. This mechanically prevents personas from hard-depending on crickets capabilities (no file under `scripts/` exists for them).

2. **No always-load** — `always_load: true` (or `always-load: true`) is rejected. Personas are on-demand; an always-load persona inflates the per-call token floor (issue #46).

3. **`tier:`**, if present, is one of `T0`/`T1`/`T2`/`T3`/`T4`.

4. **`modes:`**, if present, is a non-empty list drawn from `{sub-agent, interactive, loop, goal}`.

5. **`triggers:`**, if present, is a list of non-empty strings.

6. **`opinions:`**, if present, is a list of strings — shape only; this gate never fails a build over an opinion name that doesn't (yet) resolve to a real `opinions/<name>.md`.

`kind: persona` is also asserted; a wrong `kind:` value is a violation. `name:` and `enhances:` are **not** gate-enforced at all — a manifest with no `name:` or no `enhances:` key passes cleanly.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All personas pass every invariant — or `personas/` doesn't exist under `--root` at all ("nothing to check" is a clean pass, not an error). |
| `1` | One or more violations found (including unparseable YAML frontmatter — a malformed manifest folds into this path, not exit 2). Violations printed to stderr. |
| `2` | Setup error: unrecognized CLI flag, or a missing `pyyaml` import. |

### Gate placement

`check-personas` appears as one line in `scripts/check-all.sh` and is wired into `.github/workflows/tests-linux.yml`, `tests-mac.yml`, and `tests-windows.yml` (task 3). In `check-all.sh` itself it runs *before* `check-wiki.py` in the battery ordering — the reverse holds only in the separate `tests-linux.yml` CI step ordering.

### Unit tests

`scripts/test_check_personas.py` — 24 tests across five classes:

| Class | Tests |
|---|---|
| `TestPass` | brain-like, valid substrate requires, no personas dir, empty dir, real tree, real manifests carry all four activation axes (6 tests) |
| `TestRejectNonSubstrateRequires` | crickets capability rejected, unknown name rejected (2 tests) |
| `TestRejectAlwaysLoad` | `always_load` underscore rejected, `always-load` hyphen rejected (2 tests) |
| `TestMiscValidation` | wrong `kind:` rejected, `.sh` stem accepted (2 tests) |
| `TestActivationAxes` | valid/invalid `tier`, valid/invalid/empty `modes`, valid/empty-string `triggers`, valid/non-list/unresolvable-name `opinions`, all-axes-together, axes-absent (12 tests) |

## Related

- [Soft-Composition](Soft-Composition) — `enhances:` field that personas reuse for optional composition.
- [Persona tier design](persona-tier) — design decisions: DC-2 (positive-match kind), DC-3 (enhances: reuse), DC-4 (no-always-load invariant).
- [Capability-Resolver](Capability-Resolver) — the resolver that backs `enhances:` lookups.
- [CI-Gates](CI-Gates) — where `check-personas` appears in the gate battery.
