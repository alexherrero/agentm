# Kind-taxonomy registry + frontmatter validator reference

> [!NOTE]
> **Status: pending** — planned in `PLAN-v6-15-v6-18-typed-object-moc` (tasks 1, 2, 4), governed by [AgentM Memory Index](../designs/agentm-memory-index). Not yet built; this page reserves the shape the shipped CLIs will fill in.

`kind_registry.py` and `frontmatter_validator.py` will formalize the vault's existing free-form `kind:` frontmatter taxonomy into a recognized-set catalog + a check-only validator — the same read-only, report-not-mutate shape as [`vault_lint.py`](Vault-Lint-Checks), scoped narrowly to the `kind` value and the universal required fields rather than the full nine-check frontmatter sweep.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What will enumerate known `kind` values? | `harness/skills/memory/scripts/kind_registry.py` — `known_kinds()` + `is_known(kind)`. |
| Is the registry a closed enum? | No. It's a recognized-set catalog over a free-form field — an unrecognized `kind` is flagged, never rejected. |
| What will check a single note's frontmatter? | `frontmatter_validator.py`'s `validate(note_path)` (or a function in the same module — TBD at implementation). |
| Does either script ever rewrite a file? | No. Both are check-only, matching `vault_lint.py`'s read-only contract. |
| How will I run an audit? | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` (CLI mode, task 1c). |
| How will I check one note or the whole vault? | `python3 harness/skills/memory/scripts/frontmatter_validator.py --check <path>` / `--check-vault <vault_path>` (task 2). |
| Is this wired into CI as a hard gate? | No — advisory/report-only. See [Advisory kind-taxonomy check](#advisory-kind-taxonomy-check-in-check-allsh) below. |
| Related pages | [Audit the vault](../how-to/Audit-The-Vault) · [Vault lint checks](Vault-Lint-Checks) · [AgentM Memory Index](../designs/agentm-memory-index) |

## Registry (task 1)

`kind_registry.py` will be a stdlib-only module seeded from a real-vault frequency audit taken at plan-authoring time (2026-07-09): 47 distinct raw `kind:` values found, dominated by `preferences` (993), `workflow` (483), `idea` (222), `fix` (174). Seeding dedupes case/whitespace only — it does not semantically collapse near-synonyms (e.g. it will not merge `fix` and `bugfix` into one canonical value; that judgment call is explicitly out of scope for this plan).

Planned surface:

| Function / mode | Purpose |
|---|---|
| `known_kinds()` | Returns the recognized-set catalog — every `kind` value `save.py`, `vec_index.py`, and shipped designs actually reference, plus the seeded frequency-audit values. |
| `is_known(kind)` | `True`/`False` lookup against the catalog. |
| `audit(vault_path)` (CLI mode) | Scans a real vault; reports every distinct raw `kind:` value found, its frequency, and whether it matches strict kebab-case — flagging malformed values (parenthetical suffixes, embedded `\|`, etc.) as violations. |

The two kinds reserved by [AgentM Memory Index](../designs/agentm-memory-index) — `session-cost` and `failure-incident` — are recognized values in this same catalog, not a separate list.

## Validator (task 2)

A `validate(note_path)` function (module TBD — either `kind_registry.py` itself or a sibling `frontmatter_validator.py`) will check a single note's frontmatter:

- `kind` is a known value (via `kind_registry.is_known`), or is explicitly flagged unrecognized rather than silently accepted.
- `kind` is valid kebab-case.
- The universal required fields are present, per `save.py`'s own `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` contract (`harness/skills/memory/scripts/save.py:50`) — the same schema source `vault_lint.py`'s `required-field` and `kebab-case` checks already import, so this validator can't drift from either.

Planned CLI wrapper: `--check <path>` (one note) / `--check-vault <vault_path>` (the whole vault), reporting violations to stdout. Like `vault_lint.py`, it is never expected to write to the file it checks — task 2's own verification is a red test proving exactly that.

## How this differs from `vault_lint.py`

| | `vault_lint.py` | `kind_registry.py` + `frontmatter_validator.py` |
|---|---|---|
| Scope | Nine checks across the full frontmatter shape (field order, dates, wikilinks, supersede integrity, etc.) | `kind` value + the universal required-field trio only |
| `kind` handling | Assumes `kind` is already valid; doesn't catalog known values | The catalog itself — known vs. unrecognized, kebab-case malformed |
| Mutation | Read-only, report-only | Read-only, report-only (same contract) |
| Schema source | Imports `save.py`'s `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` | Same import, narrower use |

Once both exist, [Audit the vault](../how-to/Audit-The-Vault) is the natural place to point operators at whichever tool answers "is this note's `kind` legitimate" versus "is this note's frontmatter shape correct."

## Downstream consumer

The [MOC generator](MOC-Generator) (task 3, V6-18) depends on this registry to label each generated Map-of-Content page's group as a known kind or an unrecognized one.

## Advisory kind-taxonomy check in check-all.sh (task 4)

A `check-kind-taxonomy` step is planned for `scripts/check-all.sh`, running the task-1 `audit()` against `$MEMORY_VAULT_PATH` when it's set — graceful-skip (exit 0) when unset, the same pattern other vault-dependent checks use. Like the existing `check-slop.py --report wiki` step, it is **report-only, never a failing gate**: the real vault's 47-value mess can't be a hard PASS/FAIL yet without an operator judgment call on normalization, so violations print as a report and the step's own exit code stays 0 regardless of findings. See [CI Gates](CI-Gates) for the gate table entry once this lands.

## Related

- [Audit the vault](../how-to/Audit-The-Vault) — the operator recipe for the sibling `vault_lint.py` tool.
- [Vault lint checks reference](Vault-Lint-Checks) — the nine-check catalog this plan's registry+validator complements rather than duplicates.
- [AgentM Memory Index](../designs/agentm-memory-index) — the governing design (V6-15).
- [CI Gates](CI-Gates) — where the advisory `check-kind-taxonomy` step will be documented once shipped.
