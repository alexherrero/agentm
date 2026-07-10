# Kind-taxonomy registry + frontmatter validator reference

> [!NOTE]
> **Status: partial** — `PLAN-v6-15-v6-18-typed-object-moc` (governed by [AgentM Memory Index](../designs/agentm-memory-index)) ships this reference in two sub-deliverables. Task 1, the registry (`kind_registry.py`), is **implemented**. Task 2, the validator, is **pending** — not yet built. See the per-section status notes below.

`kind_registry.py` formalizes the vault's existing free-form `kind:` frontmatter taxonomy into a recognized-set catalog + a read-only audit CLI. `frontmatter_validator.py` (task 2, pending) will add a check-only validator on top of it — the same read-only, report-not-mutate shape as [`vault_lint.py`](Vault-Lint-Checks), scoped narrowly to the `kind` value and the universal required fields rather than the full nine-check frontmatter sweep.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What enumerates known `kind` values? | `harness/skills/memory/scripts/kind_registry.py` — `known_kinds()` (`kind_registry.py:67`) + `is_known(kind)` (`kind_registry.py:72`). |
| Is the registry a closed enum? | No. It's a recognized-set catalog over a free-form field — an unrecognized `kind` is flagged, never rejected. |
| What will check a single note's frontmatter? | `frontmatter_validator.py`'s `validate(note_path)` (or a function in the same module — TBD at implementation). **Pending — task 2, not built.** |
| Does either script ever rewrite a file? | `kind_registry.py`: no — `audit()` (`kind_registry.py:95`) is read-only by contract, confirmed by `test_audit_never_writes_to_the_vault` in `scripts/test_kind_registry.py:68`. `frontmatter_validator.py` is expected to match this contract once built (task 2). |
| How do I run an audit? | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` — works from any working directory; the `vault` argument is passed straight to `pathlib.Path()` with no cwd-relative resolution (`kind_registry.py:164-165`). |
| How will I check one note or the whole vault? | `python3 harness/skills/memory/scripts/frontmatter_validator.py --check <path>` / `--check-vault <vault_path>` (task 2, pending). |
| Is this wired into CI as a hard gate? | No — advisory/report-only. See [Advisory kind-taxonomy check](#advisory-kind-taxonomy-check-in-check-allsh) below. |
| Related pages | [Audit the vault](../how-to/Audit-The-Vault) · [Vault lint checks](Vault-Lint-Checks) · [AgentM Memory Index](../designs/agentm-memory-index) |

## Registry (task 1)

> [!NOTE]
> **Status: implemented** — shipped in `harness/skills/memory/scripts/kind_registry.py`, covered by 13 tests in `scripts/test_kind_registry.py` (`TestKnownKinds`, `TestIsKebab`, `TestAudit`).

`kind_registry.py` (`kind_registry.py:1`) is a stdlib-only module. `KNOWN_KINDS` (`kind_registry.py:33-46`, a `frozenset[str]`) is seeded from two sources: reserved values shipped code already references (`failure-incident`, `session-cost`, `crystallized`), and a frequency audit of the real vault's `personal/` + `projects/` trees taken at authoring time (2026-07-10). Near-duplicate values are kept as **separate, distinct entries deliberately** — for example both `convention` and `conventions` are known kinds — because canonicalizing near-synonyms is an explicit operator judgment call parked as its own backlog item, [agentm issue #273](https://github.com/alexherrero/agentm/issues/273), not decided by this module.

`REQUIRED_UNIVERSAL_FIELDS` (`kind_registry.py:53-55`, a `tuple[str, ...]`: `kind, status, created, updated, tags, group, slug`) also exists in this module but is **not yet consumed by anything here** — it's staged for task 2's validator to import.

Shipped surface:

| Function / mode | Signature | Purpose |
|---|---|---|
| `is_kebab(value)` | `is_kebab(value: str) -> bool` (`kind_registry.py:62`) | True iff `value` matches the kebab-case shape `save.py` itself enforces (`^[a-z0-9-]+$`). |
| `known_kinds()` | `known_kinds() -> frozenset[str]` (`kind_registry.py:67`) | Returns `KNOWN_KINDS`, the recognized-set catalog. |
| `is_known(kind)` | `is_known(kind: str) -> bool` (`kind_registry.py:72`) | Exact-match, case-sensitive lookup against `KNOWN_KINDS` — a differently-cased duplicate (e.g. `Fix`) is a distinct, unrecognized value by design. |
| `audit(vault_path)` | `audit(vault_path: Path \| str) -> dict` (`kind_registry.py:95`) | Read-only scan of a vault's `kind:` values. Returns `{"by_kind": {kind: count}, "malformed": [(path, raw_kind)], "unrecognized": [(path, raw_kind)], "total_files": int}`. Walks `personal/`, `projects/`, `_idea-incubator/` (`kind_registry.py:59`), mirroring `vec_index.py`'s `full_sync` walk roots; skips `_archive/` dirs and `PLAN.archive.*` files. `"malformed"` is a raw value failing `is_kebab()`; `"unrecognized"` is valid kebab-case but absent from `KNOWN_KINDS`. A note with no extractable `kind:` at all still counts toward `total_files` but appears in no other bucket. |
| CLI: `audit <vault>` | `python3 harness/skills/memory/scripts/kind_registry.py audit <vault_path>` (`kind_registry.py:160-173`) | Runs `audit()` against the given vault path and prints a human-readable report (`_print_report`, `kind_registry.py:145`) — total files scanned, known-kind counts by frequency, then unrecognized and malformed lists. |

The two kinds reserved by [AgentM Memory Index](../designs/agentm-memory-index) — `session-cost` and `failure-incident` — are recognized values in this same catalog, not a separate list.

## Validator (task 2)

> [!NOTE]
> **Status: pending** — no `frontmatter_validator.py` file exists yet anywhere in the repo. Not started.

A `validate(note_path)` function (module TBD — either `kind_registry.py` itself or a sibling `frontmatter_validator.py`) will check a single note's frontmatter:

- `kind` is a known value (via `kind_registry.is_known`, `kind_registry.py:72`), or is explicitly flagged unrecognized rather than silently accepted.
- `kind` is valid kebab-case (via `kind_registry.is_kebab`, `kind_registry.py:62`).
- The universal required fields are present. `kind_registry.py` already stages `REQUIRED_UNIVERSAL_FIELDS` (`kind_registry.py:53-55`) for this — a standalone tuple, not re-imported from `save.py`, mirroring `graph.py`'s standalone-module convention in this `scripts/` dir. Task 2 is expected to consume it, per `save.py`'s own `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` contract (`harness/skills/memory/scripts/save.py:50`) — the same schema source `vault_lint.py`'s `required-field` and `kebab-case` checks already import, so this validator can't drift from either.

Planned CLI wrapper: `--check <path>` (one note) / `--check-vault <vault_path>` (the whole vault), reporting violations to stdout. Like `vault_lint.py`, it is never expected to write to the file it checks — task 2's own verification is a red test proving exactly that.

## How this differs from `vault_lint.py`

| | `vault_lint.py` | `kind_registry.py` (shipped) + `frontmatter_validator.py` (pending) |
|---|---|---|
| Scope | Nine checks across the full frontmatter shape (field order, dates, wikilinks, supersede integrity, etc.) | `kind` value + the universal required-field trio only |
| `kind` handling | Assumes `kind` is already valid; doesn't catalog known values | The catalog itself — known vs. unrecognized, kebab-case malformed (shipped: `known_kinds()`, `is_known()`, `is_kebab()`) |
| Mutation | Read-only, report-only | Read-only, report-only (same contract; confirmed for `kind_registry.py` by `test_audit_never_writes_to_the_vault`) |
| Schema source | Imports `save.py`'s `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` | `kind_registry.py` keeps its own standalone `REQUIRED_UNIVERSAL_FIELDS` tuple (no import-time dependency on `save.py`); task 2's validator is expected to consume it |

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
